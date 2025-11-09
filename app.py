# app.py
import os
import json
from flask import Flask, request, jsonify, render_template
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()  # Load the .env file

# --- Configuration ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") 
try:
    client = genai.Client(api_key=GEMINI_API_KEY)
except Exception as e:
    print(f"Error initializing Gemini client: {e}")
    client = None

GAME_STATE = {} 

app = Flask(__name__)

# --- NEW: Narrative Tone Mapping ---
TONE_MAP = {
    "Sci-fi": "Clinical, technical, focused on cosmic scale, system failures, or computer logs.",
    "Medieval": "Mythic, slightly formal, referring to royalty, oaths, divine law, and ancient structures.",
    "Mythological": "Epic, archaic language, focused on fate, gods, heroes, and destiny.",
    "Horror": "Suspenseful, sensory, using first-person dread, panic, and environmental details (smell, cold).",
    "Modern": "Casual, journalistic, focused on news reports, conspiracy theories, or digital communications (text messages).",
}

# --- Gemini API Prompt & Schema ---

SYSTEM_INSTRUCTION = (
    "You are a master ARG (Alternate Reality Game) creator. Your task is to generate a complete, multi-stage, "
    "short-story ARG based on a user's chosen difficulty, genre, and a specific number of puzzles. "
    "The difficulty level must affect the complexity of the puzzles. "
    "\n\n**CRITICAL RULE:** You must double-check all ciphers, riddles, and logical puzzles. The 'puzzle_text' "
    "must logically and provably decrypt or solve to the exact 'solution'. "
    "\n\n**Strictly** adhere to the JSON schema provided for the output."
)

# ... (PUZZLE_SCHEMA and STORY_SCHEMA remain the same) ...

PUZZLE_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "puzzle_number": types.Schema(type=types.Type.INTEGER, description="The current puzzle in the sequence (e.g., 1, 2, 3...)."),
        "title": types.Schema(type=types.Type.STRING, description="A short, intriguing title for the puzzle."),
        "puzzle_text": types.Schema(type=types.Type.STRING, description="The actual riddle, cypher, logic grid instructions, or coordinate puzzle."),
        "solution": types.Schema(type=types.Type.STRING, description="The single correct answer the user must input. Case-insensitive, stripped of extra spaces/punctuation for checking."),
        "narrative_continuation": types.Schema(type=types.Type.STRING, description="The story text the user sees upon successfully solving the puzzle. This leads into the next puzzle (or the game's ending)."),
        "hint_1": types.Schema(type=types.Type.STRING, description="The first, most vague hint for the puzzle."),
        "hint_2": types.Schema(type=types.Type.STRING, description="The second, more helpful hint for the puzzle."),
        "hint_3": types.Schema(type=types.Type.STRING, description="The third, most direct hint for the puzzle."),
    },
    required=["puzzle_number", "title", "puzzle_text", "solution", "narrative_continuation", "hint_1", "hint_2", "hint_3"]
)

STORY_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "story_title": types.Schema(type=types.Type.STRING, description="A title for the entire multi-stage ARG story."),
        "introduction": types.Schema(type=types.Type.STRING, description="The opening narrative text that sets up the game and the first puzzle."),
        "puzzles": types.Schema(type=types.Type.ARRAY, items=PUZZLE_SCHEMA, description="A list of puzzle objects, matching the number requested in the prompt."),
        "ending_text": types.Schema(type=types.Type.STRING, description="The final narrative text shown after the last puzzle is solved.")
    },
    required=["story_title", "introduction", "puzzles", "ending_text"]
)

# --- Routes ---

@app.route('/')
def serve_index():
    """Serves the main HTML/JS/CSS file."""
    return render_template('index.html')

@app.route('/generate_story', methods=['POST'])
def generate_story():
    """
    Handles the user's initial choices and calls Gemini to generate the full story/puzzles.
    """
    if not client:
        return jsonify({"error": "Gemini API client not initialized. Check your API key."}), 500

    data = request.get_json()
    difficulty = data.get('difficulty')
    genre = data.get('genre')

    if not difficulty or not genre:
        return jsonify({"error": "Missing difficulty or genre."}), 400

    difficulty_map = {
        "Easy": 7,
        "Medium": 5,
        "Hard": 3
    }
    num_puzzles = difficulty_map.get(difficulty, 5) 
    narrative_tone = TONE_MAP.get(genre, "Neutral and clear.") # Get the specific tone

    print(f"Generating story: Difficulty={difficulty}, Genre={genre}, Puzzles={num_puzzles}")

    # PROMPT NOW INCLUDES TONE INSTRUCTION
    user_prompt = (
        f"Generate a complete **{num_puzzles}-puzzle** ARG story. "
        f"Difficulty: **{difficulty}**. Genre: **{genre}**. "
        f"Narrative Tone: **{narrative_tone}**. " 
        "Ensure the puzzles blend into the narrative and the difficulty level is accurately represented."
    )

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=STORY_SCHEMA,
            ),
        )

        story_data = json.loads(response.text)
        
        if len(story_data['puzzles']) != num_puzzles:
            print(f"Warning: Gemini generated {len(story_data['puzzles'])} puzzles, but {num_puzzles} were requested.")

        GAME_STATE['story'] = story_data
        GAME_STATE['current_puzzle_index'] = 0
        
        first_puzzle_index = GAME_STATE['current_puzzle_index']
        current_puzzle = story_data['puzzles'][first_puzzle_index]
        
        return jsonify({
            "success": True,
            "title": story_data['story_title'],
            "introduction": story_data['introduction'],
            "puzzle": current_puzzle,
            "puzzle_index": 1,
            "total_puzzles": len(story_data['puzzles'])
        })

    except Exception as e:
        print(f"Gemini API Error: {e}")
        return jsonify({"error": f"Failed to generate story with Gemini: {e}"}), 500


@app.route('/check_answer', methods=['POST'])
def check_answer():
    """
    Checks the user's submitted answer against the stored solution.
    """
    data = request.get_json()
    user_answer = data.get('answer', '').strip().lower()
    
    if 'story' not in GAME_STATE:
        return jsonify({"error": "Game not initialized. Please start a new game."}), 400

    current_index = GAME_STATE['current_puzzle_index']
    story_data = GAME_STATE['story']
    
    if current_index >= len(story_data['puzzles']):
        return jsonify({"success": False, "message": "Game already finished."})

    current_puzzle = story_data['puzzles'][current_index]
    
    correct_solution = current_puzzle['solution'].strip().lower()
    
    if user_answer == correct_solution:
        GAME_STATE['current_puzzle_index'] += 1
        next_index = GAME_STATE['current_puzzle_index']
        
        if next_index < len(story_data['puzzles']):
            next_puzzle = story_data['puzzles'][next_index]
            response_data = {
                "success": True,
                "status": "correct",
                "narrative": current_puzzle['narrative_continuation'],
                "puzzle": next_puzzle,
                "puzzle_index": next_index + 1
            }
        else:
            # Game is complete
            response_data = {
                "success": True,
                "status": "complete",
                "narrative": current_puzzle['narrative_continuation'], 
                "ending_text": story_data['ending_text']
            }
        
        return jsonify(response_data)
        
    else:
        return jsonify({
            "success": True,
            "status": "incorrect",
            "message": "The code is incorrect. Try again."
        })

if __name__ == '__main__':
    if not os.path.exists('templates'):
        os.makedirs('templates')
    print("Starting Flask server. Access at http://127.0.0.1:5000/")
    app.run(debug=True)