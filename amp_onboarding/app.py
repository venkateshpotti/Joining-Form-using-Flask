# app.py

import os
import uuid
import re
from flask import (
    Flask, request, render_template, redirect, url_for, jsonify, current_app, flash
)
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ConfigurationError, OperationFailure
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from werkzeug.datastructures import CombinedMultiDict # Useful for combining form and files if needed, though handled separately here
from collections import defaultdict
from datetime import datetime
import traceback # For detailed error logging

# --- Load Environment Variables ---
load_dotenv()

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Configuration Loading ---
# Load configuration from environment variables
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY')
app.config['UPLOAD_FOLDER'] = os.getenv('FLASK_UPLOAD_FOLDER', 'uploads') # Default to 'uploads'
app.config['MAX_CONTENT_LENGTH'] = int(os.getenv('FLASK_MAX_CONTENT_LENGTH', 30 * 1024 * 1024)) # Default 30MB
app.config['MONGODB_URI'] = os.getenv('MONGODB_URI')

# --- Validate Essential Configuration ---
# Ensure critical settings are present, exit if not.
if not app.config['SECRET_KEY']:
    print("\n" + "="*60)
    print("FATAL ERROR: FLASK_SECRET_KEY is not set in the environment variables.")
    print("Please generate a key (e.g., python -c 'import secrets; print(secrets.token_hex(24))')")
    print("and add it to your .env file.")
    print("="*60 + "\n")
    exit(1)
if not app.config['MONGODB_URI']:
    print("\n" + "="*60)
    print("FATAL ERROR: MONGODB_URI is not set in the environment variables.")
    print("Please add your MongoDB connection string to the .env file.")
    print("="*60 + "\n")
    exit(1)


# --- Allowed file extensions (adjust as needed) ---
ALLOWED_EXTENSIONS_DOCS = {'pdf', 'docx'}
ALLOWED_EXTENSIONS_IMAGES = {'png', 'jpg', 'jpeg', 'pdf'} # PDF often needed for image-like docs
ALLOWED_EXTENSIONS_SIGNED = {'png', 'jpg', 'jpeg', 'pdf'}

# --- Define upload subdirectories ---
UPLOAD_SUBFOLDERS = {
    'documents': 'documents',       # For ID proof, resume
    'education': 'education',       # For SSC, Inter, Grad, Additional certs
    'experience': 'experience',     # For Experience certs, Salary slips
    'insurance': 'insurance',       # For Insurance cards/docs
    'signed_docs': 'signed_docs'    # For Signed offer letter/agreement
}

# --- Ensure upload directories exist ---
# This runs once when the application starts.
try:
    base_upload_folder = app.config['UPLOAD_FOLDER']
    os.makedirs(base_upload_folder, exist_ok=True)
    for subfolder in UPLOAD_SUBFOLDERS.values():
        os.makedirs(os.path.join(base_upload_folder, subfolder), exist_ok=True)
    print(f"Upload directories checked/created under '{os.path.abspath(base_upload_folder)}'.")
except OSError as e:
    print("\n" + "="*60)
    print(f"FATAL ERROR: Could not create upload directories under '{app.config['UPLOAD_FOLDER']}': {e}")
    print("Please check file system permissions.")
    print("="*60 + "\n")
    exit(1)


# --- Database Setup Context ---
# Uses Flask's application context for better connection management.
def get_db():
    """
    Connects to the specific database using application context.
    Handles initial connection and checks validity.
    Raises RuntimeError if connection fails.
    """
    # Check if client is already stored in the app context extension storage
    if 'mongodb_client' not in app.extensions:
        mongo_uri = current_app.config['MONGODB_URI']
        masked_uri = re.sub(r':([^/]+)@', r':<password>@', mongo_uri) # Mask password for logging
        print(f"Attempting to connect to MongoDB at: {masked_uri}")
        try:
            # Set a reasonable timeout for server selection
            client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
            # The ismaster command is cheap and does not require auth. Verifies connectivity.
            client.admin.command('ismaster')
            print("MongoDB connection successful!")
            app.extensions['mongodb_client'] = client
            # Determine the database name to use
            db_name_from_uri = client.get_database().name # Get default db name from URI if present
            # Use 'employee_db' if URI doesn't specify one or specifies 'test'
            app.extensions['mongodb_db_name'] = db_name_from_uri if db_name_from_uri and db_name_from_uri != 'test' else 'employee_db'
            print(f"Using database: '{app.extensions['mongodb_db_name']}'")
        except (ConnectionFailure, ConfigurationError) as e:
            print(f"FATAL ERROR: Could not connect to MongoDB: {e}")
            traceback.print_exc()
            # Reraise as RuntimeError to indicate critical failure during startup/context creation
            raise RuntimeError(f"Failed to connect to MongoDB: {e}") from e

    # If client exists in context, ensure it's still alive before returning db handle
    try:
        client = app.extensions['mongodb_client']
        client.admin.command('ping') # Quick check if connection is alive
        db_name = app.extensions['mongodb_db_name']
        return client[db_name]
    except (ConnectionFailure, AttributeError) as e: # AttributeError if extensions were somehow cleared
        print(f"ERROR: MongoDB connection check failed or context lost: {e}")
        # Clear potentially stale context entries
        app.extensions.pop('mongodb_client', None)
        app.extensions.pop('mongodb_db_name', None)
        # Reraise to signal the issue to the calling route
        raise RuntimeError(f"MongoDB connection lost or unavailable: {e}") from e


# --- Helper Functions ---
def allowed_file(filename, allowed_extensions):
    """Checks if the file extension is allowed (case-insensitive)."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in allowed_extensions

def save_file(file_storage, subfolder_key, field_name):
    """
    Saves an uploaded FileStorage object securely.
    Determines allowed extensions based on subfolder_key/field_name.
    Returns the relative path upon success, None otherwise.
    """
    # Determine subfolder and allowed extensions based on context
    subfolder = UPLOAD_SUBFOLDERS.get(subfolder_key, UPLOAD_SUBFOLDERS['documents']) # Default to documents

    # Determine allowed extensions based on the field name or subfolder context
    allowed_extensions = ALLOWED_EXTENSIONS_IMAGES # Default is broader now
    if subfolder_key == 'education':
         allowed_extensions = ALLOWED_EXTENSIONS_IMAGES
    elif subfolder_key == 'experience':
        if field_name == 'certificate': allowed_extensions = ALLOWED_EXTENSIONS_IMAGES
        elif field_name == 'salarySlips': allowed_extensions = ALLOWED_EXTENSIONS_IMAGES
    elif subfolder_key == 'insurance':
         allowed_extensions = ALLOWED_EXTENSIONS_IMAGES
    elif subfolder_key == 'signed_docs': # For signedDocument (key will be 'signedDocument')
         allowed_extensions = ALLOWED_EXTENSIONS_SIGNED
    elif subfolder_key == 'documents': # Top level docs like idProof, resume
        if field_name == 'idProof': allowed_extensions = ALLOWED_EXTENSIONS_IMAGES
        elif field_name == 'resume': allowed_extensions = ALLOWED_EXTENSIONS_DOCS

    # Check if the file is valid and allowed
    if file_storage and file_storage.filename and allowed_file(file_storage.filename, allowed_extensions):
        original_filename = secure_filename(file_storage.filename)
        # Generate unique filename using UUID
        unique_filename = f"{uuid.uuid4().hex}_{original_filename}"
        upload_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], subfolder)
        file_path = os.path.join(upload_dir, unique_filename)
        try:
            file_storage.save(file_path)
            # Return relative path using forward slashes
            relative_path = os.path.join(subfolder, unique_filename).replace("\\", "/")
            print(f"Successfully saved file: {relative_path} (Original: {original_filename}, Field: {field_name})")
            return relative_path
        except Exception as e:
            print(f"ERROR saving file '{original_filename}' to '{file_path}': {e}")
            traceback.print_exc()
            return None # Indicate save failure
    elif file_storage and file_storage.filename:
        # Log disallowed file type attempt
        print(f"File type not allowed for '{file_storage.filename}' (Field: {field_name}). Allowed: {allowed_extensions}")
        return None # Indicate failure due to type
    else:
        # No file provided or empty filename
        return None

def parse_nested_form_data(form_dict, files_dict):
    """
    Parses form data with bracket notation (e.g., experience[0][company])
    into nested Python dictionaries and handles associated file uploads.

    Args:
        form_dict (ImmutableMultiDict): request.form data.
        files_dict (ImmutableMultiDict): request.files data.

    Returns:
        dict: A dictionary containing the structured form data and file paths.
    """
    parsed_data = defaultdict(lambda: defaultdict(dict)) # For dict structures like education[ssc][field]
    parsed_lists = defaultdict(list)                     # For list structures like experience[0][field]
    single_files = {}                                    # For top-level files like idProof
    other_data = {}                                      # For simple top-level fields

    # Regex to robustly capture section, index/key (optional), and field (optional)
    pattern = re.compile(r"^(\w+)(?:\[(.*?)\])?(?:\[(.*?)\])?$")

    # --- Process Text/Select/Radio/etc. Form Fields ---
    for key, value in form_dict.items():
        match = pattern.match(key)
        if not match:
            other_data[key] = value
            continue

        section, index_or_key, field = match.groups()

        if index_or_key is None and field is None: # Simple field: 'firstName'
             other_data[section] = value
        elif field is None: # Dict key only: 'education[ssc]' (unlikely structure for this form)
             parsed_data[section][index_or_key] = value
        elif index_or_key.isdigit(): # List item: 'experience[0][company]'
            index = int(index_or_key)
            while len(parsed_lists[section]) <= index: parsed_lists[section].append({})
            parsed_lists[section][index][field] = value
        else: # Dictionary item: 'education[ssc][school]'
            parsed_data[section][index_or_key][field] = value

    # --- Process File Uploads ---
    processed_file_keys = set()

    for key in files_dict:
        if key in processed_file_keys:
            continue

        file_list = files_dict.getlist(key)
        if not file_list or all(not f or not f.filename for f in file_list): # Check if FileStorage exists and has filename
            continue

        match = pattern.match(key)
        if not match:
            print(f"Warning: Key '{key}' did not match expected file pattern. Skipping.")
            processed_file_keys.add(key)
            continue

        section, index_or_key, field = match.groups()

        # ******** Correctly handle top-level vs nested files ********
        is_top_level_file = (index_or_key is None and field is None)

        if is_top_level_file:
            # Handle top-level files (e.g., 'idProof', 'resume', 'signedDocument')
            if len(file_list) > 1:
                print(f"Warning: Multiple files received for top-level field '{key}'. Using only the first.")
            file_storage = file_list[0]
            if file_storage and file_storage.filename:
                # 'section' variable holds the field name (key) here
                field_name = section
                # Determine subfolder based on the field name
                subfolder_key = 'documents' # Default
                if field_name == 'signedDocument': subfolder_key = 'signed_docs'
                # Use save_file to determine allowed types and save
                file_path = save_file(file_storage, subfolder_key, field_name)
                if file_path:
                    single_files[key] = file_path # Store using the original key
            processed_file_keys.add(key)

        # --- Process Nested Files ---
        elif field is None: # Files must have a field name within brackets
            print(f"Warning: File received for key '{key}' without required field specifier in brackets. Skipping.")
            processed_file_keys.add(key)
        else: # This is a nested file (index_or_key and field are present)
            subfolder_key = section # Base subfolder on the section name
            field_name = field      # Use field name for context in save_file
            is_multiple_expected = (section == 'experience' and field == 'salarySlips')

            saved_paths = []
            for file_storage in file_list:
                if file_storage and file_storage.filename:
                    file_path = save_file(file_storage, subfolder_key, field_name)
                    if file_path:
                        saved_paths.append(file_path)
                    else:
                        print(f"Warning: Failed to save one of the files for field '{key}'.") # Log failure for specific file

            if not saved_paths:
                # Log if no files were saved for a field that had uploads attempted
                if any(f and f.filename for f in file_list): # Check if there were actual files attempted
                     print(f"Warning: No files were successfully saved for field '{key}' (check allowed types/permissions).")
                processed_file_keys.add(key)
                continue # Skip adding this field if no files saved

            value_to_store = saved_paths if is_multiple_expected else saved_paths[0]

            if index_or_key.isdigit(): # List item: 'experience[0][certificate]'
                index = int(index_or_key)
                storage_target = parsed_lists[section]
                while len(storage_target) <= index: storage_target.append({})
                storage_target[index][field] = value_to_store
            else: # Dictionary item: 'education[ssc][certificate]'
                storage_target = parsed_data[section][index_or_key]
                storage_target[field] = value_to_store # Always single file for education certs

            processed_file_keys.add(key)
        # ******** END FIX ********

    # --- Combine all parsed data into the final structure ---
    final_submission = {**other_data}
    for section, content in parsed_data.items():
         final_submission[section] = {k: dict(v) for k, v in content.items()}
    for section, content_list in parsed_lists.items():
         final_submission[section] = [item for item in content_list if item]
    final_submission.update(single_files)

    return final_submission


# --- Routes ---
@app.route('/')
def index():
    """Displays the onboarding form."""
    try:
        get_db()
        print("Index route: DB connection check successful.")
    except Exception as e:
         print(f"ERROR rendering index: Failed to get DB connection: {e}")
         flash(f"Error connecting to the database. Please contact the administrator. ({type(e).__name__})", "danger")
    return render_template('index.html')

@app.route('/submit', methods=['POST'])
def submit_form():
    """Handles form submission, parsing, file saving, and database insertion."""
    try:
        db = get_db()
        onboarding_collection = db.onboarding_forms
    except RuntimeError as e:
         print(f"ERROR in /submit: Failed to get DB connection: {e}")
         return jsonify({"success": False, "error": "Database connection failed. Please try again later or contact support."}), 500
    except Exception as e:
        print(f"CRITICAL ERROR in /submit during DB init: {e}")
        traceback.print_exc()
        return jsonify({"success": False, "error": "A critical server error occurred [DB Init]. Please contact support."}), 500


    if request.method == 'POST':
        try:
            form_data = request.form
            file_data = request.files

            # --- Parse form data and save files ---
            parsed_submission = parse_nested_form_data(form_data, file_data)

            # --- Check if required files were successfully parsed and saved ---
            required_file_keys = ['idProof', 'resume', 'signedDocument']
            missing_required_files = [rfk for rfk in required_file_keys if rfk not in parsed_submission]

            # Check nested required files based on conditional checkboxes
            # Use form_data to check if the section *should* have been submitted
            # Because parsed_submission might not have the key if *no* data was entered for that section
            if 'education[ssc][school]' in form_data: # Check if SSC section was likely submitted
                if not parsed_submission.get('education', {}).get('ssc', {}).get('certificate'):
                    missing_required_files.append(f'Education Certificate (SSC)')
            if 'education[inter][college]' in form_data: # Check if Inter section was likely submitted
                if not parsed_submission.get('education', {}).get('inter', {}).get('certificate'):
                    missing_required_files.append(f'Education Certificate (Inter/Diploma)')
            if 'education[grad][college]' in form_data: # Check if Grad section was likely submitted
                if not parsed_submission.get('education', {}).get('grad', {}).get('certificate'):
                    missing_required_files.append(f'Education Certificate (Graduation)')

            # Check additional education based on presence in parsed data (means it was added via JS)
            if parsed_submission.get('additionalEducation'):
                for i, add_edu in enumerate(parsed_submission['additionalEducation']):
                    if not add_edu.get('certificate'):
                        missing_required_files.append(f'Additional Education Certificate {i+1}')

            # Check experience certs ONLY IF hasExperience checkbox was checked in the *original form data*
            if 'hasExperience' in form_data and parsed_submission.get('experience'):
                 for i, exp in enumerate(parsed_submission['experience']):
                     if not exp.get('certificate'):
                          missing_required_files.append(f'Experience Certificate {i+1}')

            # Check insurance docs ONLY IF hasInsurance checkbox was checked
            if 'hasInsurance' in form_data and parsed_submission.get('insurance'):
                 for i, ins in enumerate(parsed_submission['insurance']):
                      if not ins.get('document'):
                           missing_required_files.append(f'Insurance Document {i+1}')


            if missing_required_files:
                 error_message = f"Missing or invalid required file(s): {', '.join(sorted(list(set(missing_required_files))))}. Please check file types/uploads and try again."
                 print(f"Submission failed due to missing/invalid files: {error_message}")
                 return jsonify({"success": False, "error": error_message}), 400 # 400 Bad Request


            # --- Data Cleaning / Type Conversion (Continue only if files are okay) ---
            for key in ['sameAsPermanent', 'hasExperience', 'hasInsurance', 'agreeTerms', 'agreePrivacy']:
                parsed_submission[key] = key in form_data

            if 'experience' in parsed_submission and isinstance(parsed_submission['experience'], list):
                for i, exp in enumerate(parsed_submission['experience']):
                     form_key = f"experience[{i}][currentJob]"
                     exp['currentJob'] = form_key in form_data

            # Convert relevant date strings to datetime objects
            date_keys_to_check = ['dateOfBirth', 'signatureDate']
            # Use helper functions to get/set nested values safely
            def get_nested_value(data, key_path):
                keys = key_path.split('.')
                current = data
                for key in keys:
                    if key.isdigit(): key = int(key)
                    if isinstance(current, list) and isinstance(key, int) and key < len(current): current = current[key]
                    elif isinstance(current, dict) and key in current: current = current[key]
                    else: return None
                return current

            def set_nested_value(data, key_path, value):
                keys = key_path.split('.')
                current = data
                for i, key in enumerate(keys[:-1]):
                    if key.isdigit(): key = int(key)
                    if isinstance(current, list) and isinstance(key, int) and key < len(current): current = current[key]
                    elif isinstance(current, dict) and key in current: current = current[key]
                    else: return False # Path doesn't exist
                last_key = keys[-1]
                if last_key.isdigit(): last_key = int(last_key)
                if isinstance(current, dict): current[last_key] = value; return True
                elif isinstance(current, list) and isinstance(last_key, int) and last_key < len(current): current[last_key] = value; return True
                return False

            # Add nested date keys to check list dynamically
            for i, exp_item in enumerate(parsed_submission.get('experience', [])):
                if get_nested_value(exp_item, 'startDate'): date_keys_to_check.append(f'experience.{i}.startDate')
                if get_nested_value(exp_item, 'endDate'): date_keys_to_check.append(f'experience.{i}.endDate')
            for i, ins_item in enumerate(parsed_submission.get('insurance', [])):
                if get_nested_value(ins_item, 'expirationDate'): date_keys_to_check.append(f'insurance.{i}.expirationDate')
            # Add additional education dates if they existed

            for key_path in date_keys_to_check:
                 date_str = get_nested_value(parsed_submission, key_path)
                 if date_str and isinstance(date_str, str):
                     try:
                         date_obj = datetime.strptime(date_str, '%Y-%m-%d')
                         if not set_nested_value(parsed_submission, key_path, date_obj):
                              print(f"Warning: Could not set nested date value for {key_path}")
                     except ValueError:
                         print(f"Warning: Could not parse date string '{date_str}' for key '{key_path}'. Storing as string.")


            # Add submission timestamp
            parsed_submission['submitted_at'] = datetime.utcnow()

            # --- Insert into MongoDB ---
            print("\n--- Attempting to Insert Data into MongoDB ---")
            # print(f"Data: {parsed_submission}") # Use with caution in prod
            print("-------------------------------------------\n")

            insert_result = onboarding_collection.insert_one(parsed_submission)

            if insert_result.inserted_id:
                print(f"Successfully inserted document with MongoDB _id: {insert_result.inserted_id}")
                return jsonify({"success": True, "message": "Form submitted successfully!"}), 200
            else:
                 print("ERROR: Database insertion command executed but reported no inserted ID.")
                 return jsonify({"success": False, "error": "Failed to save data to database (no ID returned)."}), 500

        # --- Specific Error Handling ---
        except OperationFailure as e:
            print(f"ERROR: MongoDB Operation Failure during submission: {e.details}")
            traceback.print_exc()
            error_msg = f"Database operation failed: {e.details.get('errmsg', 'Unknown database error')}"
            return jsonify({"success": False, "error": error_msg}), 500
        except Exception as e:
            print(f"ERROR: An unexpected error occurred during form submission: {e}")
            traceback.print_exc()
            return jsonify({"success": False, "error": "An internal server error occurred processing your request. Please contact support."}), 500

    # If method is not POST
    return redirect(url_for('index'))


# --- Teardown Context ---
@app.teardown_appcontext
def teardown_db(exception=None):
    """Closes the database connection when the app context tears down."""
    client = app.extensions.pop('mongodb_client', None)
    app.extensions.pop('mongodb_db_name', None)
    if client is not None:
        client.close()
        print("MongoDB connection closed.")
    if exception:
        print(f"App context teardown due to exception: {exception}")


# --- Run the App ---
if __name__ == '__main__':
    is_debug = os.getenv('FLASK_DEBUG', '0').lower() in ['1', 'true', 'yes']
    port = int(os.getenv('PORT', 5004)) # Use PORT env var or default to 5004

    print(f"\nStarting Employee Onboarding Flask app (Debug Mode: {is_debug})...")
    print(f"Uploads will be saved under: {os.path.abspath(app.config['UPLOAD_FOLDER'])}")

    print("Performing initial database connection check...")
    try:
        with app.app_context():
            get_db()
    except Exception as e:
        print("\n" + "="*60)
        print(f"CRITICAL: Initial Database connection check failed. The application cannot start.")
        print(f"Error details: {e}")
        print("Please check your MONGODB_URI in the .env file, database server status,")
        print("network access (firewalls, IP whitelisting for Atlas), and credentials.")
        print("="*60 + "\n")
        exit(1)

    print(f"Flask server starting on http://0.0.0.0:{port}")
    app.run(host='0.0.0.0', port=port, debug=is_debug)