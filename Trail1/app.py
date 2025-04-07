

import os
import logging
import pymongo
from flask import Flask, render_template, request, redirect, url_for, flash
from pymongo import MongoClient
from bson import ObjectId
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
import traceback

# Load environment variables from .env file
load_dotenv()

# Initialize Flask App
app = Flask(__name__)
# --- ### VERY IMPORTANT: Set a strong SECRET_KEY in .env ### ---
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'this-is-really-insecure-please-change')
if app.config['SECRET_KEY'] == 'this-is-really-insecure-please-change':
     print("\n!!!! WARNING: Default SECRET_KEY detected. Set a strong one in .env !!!!\n")


# --- Configuration ---
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'pdf'}
# --- ### VERY IMPORTANT: Verify these values match your .env and MongoDB setup ### ---
MONGO_URI = os.environ.get('MONGO_URI')
DATABASE_NAME = os.environ.get('DATABASE_NAME', "job_application_db")
COLLECTION_NAME = "applications" # Assuming collection name is 'applications'

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Ensure upload folder exists
if not os.path.exists(UPLOAD_FOLDER):
    try:
        os.makedirs(UPLOAD_FOLDER)
        logging.info(f"Created upload folder: {UPLOAD_FOLDER}")
    except OSError as e:
        logging.error(f"Error creating upload folder {UPLOAD_FOLDER}: {e}", exc_info=True)

# Configure Logging (DEBUG level for detailed output)
log_level = logging.DEBUG
logging.basicConfig(level=log_level,
                    format='%(asctime)s [%(levelname)s] %(name)s %(lineno)d: %(message)s')
logger = logging.getLogger(__name__)

# --- MongoDB Connection and Unique Index Setup ---
db = None
applications_collection = None
is_db_connected = False
unique_index_active = False # Tracks if the DB index is likely enforcing uniqueness

if not MONGO_URI:
    logger.critical("FATAL: MONGO_URI not found. Database operations impossible.")
else:
    try:
        logger.info(f"Connecting to MongoDB: DB='{DATABASE_NAME}', Collection='{COLLECTION_NAME}'...")
        # Increased timeouts slightly
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=7000, connectTimeoutMS=10000)
        client.admin.command('ping') # Verify connection is live
        db = client[DATABASE_NAME]
        applications_collection = db[COLLECTION_NAME]
        is_db_connected = True
        logger.info("MongoDB connection successful.")

        # --- ### Attempt to Ensure Unique Email Index ### ---
        index_name = "email_enforce_unique_idx" # Use a clear, potentially new name
        logger.info(f"Ensuring unique index '{index_name}' exists on 'email' field...")
        try:
            applications_collection.create_index(
                [("email", pymongo.ASCENDING)],
                name=index_name,
                unique=True
            )
            # Verify index status AFTER creation attempt
            index_info = applications_collection.index_information()
            if index_name in index_info and index_info[index_name].get('unique'):
                unique_index_active = True
                logger.info(f"SUCCESS: Database unique index '{index_name}' confirmed active.")
            else:
                logger.critical(f"CRITICAL: Failed to confirm unique index '{index_name}' status after creation attempt. Index Info: {index_info}")

        except pymongo.errors.OperationFailure as e:
            # Specific handling for common index creation errors
            if "duplicate key error collection" in str(e) and "index build" in str(e):
                 logger.critical(f"CRITICAL FAILURE: Cannot create unique index '{index_name}' - DUPLICATE EMAILS EXIST in '{COLLECTION_NAME}'.")
                 logger.critical("!!! MANUALLY REMOVE DUPLICATE EMAILS FROM DATABASE !!!")
            elif "already exists with different options" in str(e) or e.code == 85:
                 logger.warning(f"Index '{index_name}' or similar might exist with different options. Checking if *any* unique index on 'email' is active...")
                 try:
                     index_info = applications_collection.index_information()
                     found_unique = False
                     for name, info in index_info.items():
                         if info.get('key') == [('email', 1)] and info.get('unique'):
                             logger.info(f"CONFIRMED: Existing unique index '{name}' found and active.")
                             unique_index_active = True
                             found_unique = True
                             break
                     if not found_unique:
                          logger.critical("CRITICAL: Could not confirm *any* active unique index on 'email' field.")
                 except Exception as verify_e:
                      logger.error(f"Error verifying existing index: {verify_e}", exc_info=True)
            else:
                logger.critical(f"CRITICAL FAILURE creating unique index '{index_name}': {e}", exc_info=True)

        if not unique_index_active:
             logger.warning("!!! Warning: Database unique index on 'email' is NOT confirmed active. Uniqueness relies solely on the application pre-check. !!!")

    except pymongo.errors.ConnectionFailure as e:
        logger.critical(f"MongoDB Connection Failure: {e}", exc_info=True)
        is_db_connected = False
    except Exception as e:
        logger.critical(f"Unexpected error during MongoDB setup: {e}", exc_info=True)
        is_db_connected = False

# --- Helper Function ---
def allowed_file(filename):
    """Checks if the filename has an allowed extension."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- Routes ---
@app.route('/', methods=['GET', 'POST'])
def job_application_form():
    logger.debug(f"Route '/' - Method: {request.method}")
    error = None
    # Capture original data for repopulation on ANY error
    form_data_repop = request.form.to_dict() if request.method == 'POST' else {}

    if request.method == 'POST':
        logger.info("Processing POST request.")

        # --- Basic DB Connection Check ---
        if not is_db_connected or applications_collection is None:
            logger.error("Database connection is not available. Aborting submission.")
            flash("Server error: Database connection failed. Please try again later.", "danger")
            return render_template('form.html', error="Database unavailable.", form_data=form_data_repop)

        form_data = request.form.to_dict()

        # --- ### 1. Standardize Email ### ---
        raw_email = form_data.get('email', '')
        standardized_email = raw_email.strip().lower()
        form_data['email'] = standardized_email # Use this standardized form going forward
        logger.debug(f"Standardized email for checking: '{standardized_email}'")

        # --- File Handling ---
        # ... (file handling logic as before) ...
        files_to_process = {
            'sscDoc': request.files.get('sscDoc'),
            'intermediateDoc': request.files.get('intermediateDoc'),
            'graduationDoc': request.files.get('graduationDoc'),
            'Additionalfiles': request.files.get('Additionalfiles')
        }
        file_paths = {}
        file_validation_error = None

        for field, file_storage in files_to_process.items():
             is_required = field != 'Additionalfiles'
             if file_storage and file_storage.filename:
                 if allowed_file(file_storage.filename):
                     filename = secure_filename(file_storage.filename)
                     unique_filename = f"{ObjectId()}_{filename}"
                     file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                     try:
                         file_storage.save(file_path)
                         file_paths[field] = file_path
                     except Exception as e:
                         logger.error(f"Error saving file {field}: {e}", exc_info=True)
                         file_validation_error = f"Could not save file: {filename}."
                         break
                 else:
                     logger.warning(f"Invalid file type: {file_storage.filename}")
                     file_validation_error = "Only PDF files allowed."
                     break
             elif is_required:
                 logger.warning(f"Missing required file: {field}")
                 file_validation_error = f"{field.replace('Doc',' Doc.')} is required."
                 break

        if file_validation_error:
            logger.error(f"File validation failed: {file_validation_error}")
            flash(file_validation_error, "danger")
            return render_template('form.html', error=file_validation_error, form_data=form_data_repop)

        form_data.update(file_paths)


        # --- ### 2. Server-side Data Validation ### ---
        # ... (Your existing required field checks, type checks, etc.) ...
        validation_errors = []
        if not standardized_email: # Check standardized email
            validation_errors.append("Email is required.")
        elif '@' not in standardized_email or '.' not in standardized_email.split('@')[-1]:
             validation_errors.append("Invalid email format.")
        # Add other validations...

        if validation_errors:
            error_message = "Please correct errors: " + " ".join(validation_errors)
            logger.warning(f"Form validation failed: {error_message}")
            flash(error_message, "danger")
            return render_template('form.html', error=error_message, form_data=form_data_repop)


        # --- ### 3. Explicit Application-Level Email Check ### ---
        logger.debug(f"Performing explicit check for existing email: '{standardized_email}'")
        try:
            existing_application = applications_collection.find_one({"email": standardized_email})

            if existing_application:
                # --- EMAIL FOUND - BLOCK SUBMISSION ---
                logger.warning(f"Duplicate email detected by application pre-check: '{standardized_email}' (Existing Doc ID: {existing_application.get('_id')}). Submission BLOCKED.")
                error_msg = f"An application with the email address '{standardized_email}' already exists."
                flash(error_msg, "danger")
                # Return the original form data
                return render_template('form.html', error="Email already registered.", form_data=form_data_repop)
            else:
                # --- EMAIL NOT FOUND - PROCEED TO INSERT ATTEMPT ---
                logger.debug(f"Email '{standardized_email}' not found by pre-check. Proceeding to database insert attempt.")

        except pymongo.errors.PyMongoError as e:
            logger.exception("Database error during email pre-check query.")
            flash("Server error checking email existence. Please try again.", "danger")
            return render_template('form.html', error="Server Check Error.", form_data=form_data_repop)
        except Exception as e:
             logger.exception("Unexpected error during email pre-check.")
             flash("An unexpected server error occurred. Please try again.", "danger")
             return render_template('form.html', error="Unexpected Check Error.", form_data=form_data_repop)


        # --- ### 4. Attempt Database Insert ### ---
        # This block only runs if the email pre-check passed (email not found by find_one)
        logger.info(f"Attempting database insert for email: '{standardized_email}' (Pre-check passed)...")
        try:
            form_data.pop('_id', None) # Ensure no _id field

            result = applications_collection.insert_one(form_data)
            inserted_id = result.inserted_id

            logger.info(f"SUCCESSFUL INSERT: Document ID: {inserted_id} for email: '{standardized_email}'")
            flash('Application submitted successfully!', 'success')
            return redirect(url_for('success', id=str(inserted_id)))

        # --- ### 5. Handle Database DuplicateKeyError (Safety Net) ### ---
        except pymongo.errors.DuplicateKeyError as e:
            # This *should* only happen if the pre-check missed it (race condition) AND the index is active.
            logger.warning(f"DuplicateKeyError caught during INSERT for email '{standardized_email}'. Database index prevented duplicate. Details: {e.details}", exc_info=True)
            error_msg = f"Submission failed: The email address '{standardized_email}' was registered just before your submission completed."
            flash(error_msg, "danger")
            return render_template('form.html', error="Email registered concurrently.", form_data=form_data_repop)

        # --- Other potential errors during insert ---
        except pymongo.errors.ConnectionFailure as e:
            logger.exception("MongoDB connection failure during insert.")
            flash("Database Error: Connection lost during save.", "danger")
            return render_template('form.html', error="DB Connection Error.", form_data=form_data_repop)
        except pymongo.errors.PyMongoError as e:
            logger.exception(f"MongoDB error during insert: {type(e).__name__}")
            flash(f"Database Error: Could not save data ({type(e).__name__}).", "danger")
            return render_template('form.html', error="DB Save Error.", form_data=form_data_repop)
        except Exception as e:
            logger.exception("Unexpected error saving form data.")
            flash(f"Server Error: An unexpected error occurred ({type(e).__name__}).", "danger")
            return render_template('form.html', error="Unexpected Server Error.", form_data=form_data_repop)

    # --- GET Request ---
    logger.debug("Rendering form for GET request.")
    return render_template('form.html', error=None, form_data=None)


@app.route('/success')
def success():
    # (Success route remains the same - displays data if found)
    inserted_id_str = request.args.get('id')
    logger.info(f"Accessing success page for ID: {inserted_id_str}")
    application_data = None
    fetch_error = None

    if not inserted_id_str:
        logger.warning("Success page accessed without ID.")
    elif not is_db_connected or applications_collection is None: # Check simple connection
        logger.error("DB not connected on success page.")
        fetch_error = "Database error retrieving details."
        flash("Database issue retrieving details.", "warning")
    else:
        try:
            if not ObjectId.is_valid(inserted_id_str):
                 logger.warning(f"Invalid ObjectId format: {inserted_id_str}")
                 fetch_error = "Invalid application reference."
                 flash(fetch_error, "danger")
            else:
                object_id_to_find = ObjectId(inserted_id_str)
                application_data = applications_collection.find_one({"_id": object_id_to_find})
                if application_data:
                    logger.debug(f"Fetched data for ID {inserted_id_str}.")
                else:
                    logger.warning(f"Data NOT found for ID: {inserted_id_str}")
                    fetch_error = "Application data not found."
                    flash("Could not retrieve submission details.", "warning")
        except Exception as e:
            logger.exception(f"Error fetching success data for ID {inserted_id_str}")
            fetch_error = "Error retrieving submission details."
            flash(fetch_error, "danger")

    return render_template('success.html', application=application_data, error=fetch_error)

# --- Main Execution ---
if __name__ == '__main__':
    host = os.environ.get('FLASK_RUN_HOST', '0.0.0.0')
    port = int(os.environ.get('FLASK_RUN_PORT', 5001))
    debug_mode = os.environ.get('FLASK_ENV', 'development').lower() == 'development'

    logger.info(f"--- Starting Application ---")
    logger.info(f"Flask Env: {os.environ.get('FLASK_ENV', 'production')}, Debug Mode: {debug_mode}")
    logger.info(f"Listening on http://{host}:{port}/")
    if not is_db_connected:
         logger.critical("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
         logger.critical("!!! FATAL: Application starting WITHOUT DB CONNECTION !!!")
         logger.critical("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
    elif not unique_index_active:
         logger.warning("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
         logger.warning("!!! WARNING: DB Connected, but UNIQUE EMAIL INDEX IS NOT CONFIRMED ACTIVE !!!")
         logger.warning("!!! Uniqueness relies ONLY on application pre-check. Check startup logs !!!")
         logger.warning("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")

    # Use debug=debug_mode for Flask's reloader/debugger
    app.run(debug=debug_mode, host=host, port=port)








# import os
# import logging
# import pymongo
# from flask import Flask, render_template, request, redirect, url_for, flash
# from pymongo import MongoClient
# from bson import ObjectId
# from dotenv import load_dotenv
# from werkzeug.utils import secure_filename

# # Load environment variables from .env file
# load_dotenv()

# # Initialize Flask App
# app = Flask(__name__)
# app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'a_default_secret_key_for_dev') # Use env var or default

# # --- Configuration ---
# UPLOAD_FOLDER = 'uploads'
# ALLOWED_EXTENSIONS = {'pdf'}
# MONGO_URI = os.environ.get('MONGO_URI')
# DATABASE_NAME = "job_application_db"

# app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# # Ensure the upload folder exists
# if not os.path.exists(UPLOAD_FOLDER):
#     try:
#         os.makedirs(UPLOAD_FOLDER)
#         logging.info(f"Created upload folder: {UPLOAD_FOLDER}")
#     except OSError as e:
#         logging.error(f"Error creating upload folder {UPLOAD_FOLDER}: {e}")
#         # Decide if the app can run without the upload folder
#         # exit() or handle appropriately

# # Configure Logging
# logging.basicConfig(level=logging.DEBUG,
#                     format='%(asctime)s - %(levelname)s - %(message)s')

# # --- MongoDB Connection ---
# db = None
# applications_collection = None # Initialize as None
# if not MONGO_URI:
#     logging.error("MONGO_URI not found in environment variables. Database functionality will be disabled.")
# else:
#     try:
#         client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000) # Add timeout
#         # The ismaster command is cheap and does not require auth. Confirms connection.
#         client.admin.command('ismaster')
#         db = client[DATABASE_NAME]
#         applications_collection = db["applications"] # Assign the collection object here
#         # Optional: Create a unique index on email if you want to prevent duplicates server-side
#         # applications_collection.create_index([("email", pymongo.ASCENDING)], unique=True)
#         logging.info("Connected to MongoDB successfully!")
#     except pymongo.errors.ConnectionFailure as e:
#         logging.error(f"Could not connect to MongoDB at {MONGO_URI}: {e}")
#         # applications_collection remains None
#     except Exception as e:
#         logging.error(f"An error occurred during MongoDB setup: {e}")
#         # applications_collection remains None

# # --- Helper Function ---
# def allowed_file(filename):
#     """Checks if the filename has an allowed extension."""
#     return '.' in filename and \
#            filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# # --- Routes ---
# @app.route('/', methods=['GET', 'POST'])
# def job_application_form():
#     """Handles the job application form display (GET) and submission (POST)."""
#     logging.debug(f"Request method: {request.method}")
#     error = None # Initialize error message
#     form_data_repop = request.form.to_dict() # Use this for repopulating on error

#     if request.method == 'POST':
#         logging.debug("Received POST request")

#         # Check if database connection is available
#         # ***** FIX APPLIED HERE *****
#         if applications_collection is None:
#             logging.error("Database collection is not available.")
#             error = "Database connection error. Please try again later."
#             # Pass back submitted data for repopulation
#             return render_template('form.html', error=error, form_data=form_data_repop)

#         form_data = request.form.to_dict()
#         logging.debug(f"Form data received: {form_data}")
#         logging.debug(f"Files received: {request.files}")

#         # --- File Handling ---
#         files_to_process = {
#             'sscDoc': request.files.get('sscDoc'),
#             'intermediateDoc': request.files.get('intermediateDoc'),
#             'graduationDoc': request.files.get('graduationDoc'),
#             'Additionalfiles': request.files.get('Additionalfiles') # Optional file
#         }

#         file_paths = {}
#         validation_error_occurred = False

#         for field, file_storage in files_to_process.items():
#             is_required = field != 'Additionalfiles' # Additionalfiles is optional

#             if file_storage and file_storage.filename: # Check if FileStorage exists and has a filename
#                 if allowed_file(file_storage.filename):
#                     filename = secure_filename(file_storage.filename)
#                     # Consider making filenames unique to prevent overwrites
#                     unique_filename = f"{ObjectId()}_{filename}"
#                     file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
#                     try:
#                         file_storage.save(file_path)
#                         # Store the path relative to the app or a designated access point if needed later
#                         # For simplicity, storing the saved path here. Might store just unique_filename.
#                         file_paths[field] = file_path
#                         logging.debug(f"Saved file {field} to {file_path}")
#                     except Exception as e:
#                          logging.error(f"Error saving file {field} ('{filename}'): {e}")
#                          error = f"Could not save file: {filename}. Please ensure the server has permission and disk space."
#                          validation_error_occurred = True
#                          break # Stop processing further files on save error
#                 else:
#                     logging.error(f"Invalid file type for {field}: {file_storage.filename}")
#                     # Capitalize field name nicely for error message
#                     field_name_display = field.replace('Doc',' Document').replace('files', ' Files')
#                     error = f"{field_name_display}: Only PDF files are allowed."
#                     validation_error_occurred = True
#                     break # Stop processing on invalid file type
#             elif is_required:
#                 # File is required but not provided or filename is empty
#                 logging.error(f"Missing required file: {field}")
#                 field_name_display = field.replace('Doc',' Document')
#                 error = f"{field_name_display} is required. Please upload a PDF file."
#                 validation_error_occurred = True
#                 break # Stop processing on missing required file

#         if validation_error_occurred:
#              # Repopulate form with originally submitted data and show file error
#             return render_template('form.html', error=error, form_data=form_data_repop)

#         # Update form_data with file paths (or just filenames if preferred)
#         form_data.update(file_paths) # Now form_data contains text fields + paths to saved files

#         # --- Server-side Data Validation ---
#         base_required_fields = [
#             'department', 'jobRole', 'branchLocation', 'expectedSalary', 'interviewDate',
#             'joiningDate', 'employmentType', 'FullName', 'email', 'dob', 'mobileNumber',
#             'fatherName', 'permanentAddress', 'sscYear', 'sscPercentage', 'intermediateYear',
#             'intermediatePercentage', 'higherGraduation', 'registerNumber', 'graduationYear',
#             'graduationPercentage', 'experienceStatus'
#             # File fields (sscDoc, intermediateDoc, graduationDoc) validated during file handling above
#         ]

#         # Add conditional experience fields if status is 'experience'
#         experience_status = form_data.get('experienceStatus')
#         required_fields = base_required_fields[:] # Create a copy

#         if experience_status == 'experience':
#             experience_required = ['yearsOfExperience', 'previousCompany', 'previousJobRole']
#             required_fields.extend(experience_required)
#         elif experience_status == 'fresher':
#              # Remove any potentially submitted experience data if fresher (to avoid saving inconsistent data)
#             form_data.pop('yearsOfExperience', None)
#             form_data.pop('previousCompany', None)
#             form_data.pop('previousJobRole', None)
#         else:
#             # Handle case where experienceStatus might be missing (shouldn't happen with 'required' in HTML)
#             error = "Experience Status is required."
#             logging.error(error + f" Data: {form_data}")
#             return render_template('form.html', error=error, form_data=form_data_repop)


#         missing_fields = []
#         for field in required_fields:
#             # Check if field exists and has a non-empty value after stripping whitespace
#             value = form_data.get(field)
#             if value is None or not str(value).strip():
#                  # Simple capitalization/spacing for display - adapt as needed
#                  field_display = field.replace('_', ' ').replace('Doc', ' Document').title()
#                  missing_fields.append(field_display)

#         if missing_fields:
#             error = f"Missing required fields: {', '.join(missing_fields)}."
#             logging.error(f"Validation Error: {error} Data: {form_data}")
#             return render_template('form.html', error=error, form_data=form_data_repop)

#         # --- Data Type/Format Validation (Examples) ---
#         try:
#             salary = int(form_data.get('expectedSalary', 0))
#             if salary < 100000:
#                 error="Expected Salary must be at least 100,000."
#                 logging.error(error + f" Value: {form_data.get('expectedSalary')}")
#                 return render_template('form.html', error=error, form_data=form_data_repop)
#         except (ValueError, TypeError):
#             error="Expected Salary must be a valid number."
#             logging.error(error + f" Value: {form_data.get('expectedSalary')}")
#             return render_template('form.html', error=error, form_data=form_data_repop)

#         # Basic Email Format Check (consider using a library like 'email_validator' for robust check)
#         email = form_data.get('email', '')
#         if '@' not in email or '.' not in email.split('@')[-1] or len(email.split('@')) > 2:
#              error="Invalid email format provided."
#              logging.error(error + f" Value: {email}")
#              return render_template('form.html', error=error, form_data=form_data_repop)

#         # Add more specific validations here (phone format, date ranges, year sequences, etc.)
#         # Example: Check year sequence basic
#         try:
#             ssc_y = int(form_data.get('sscYear'))
#             int_y = int(form_data.get('intermediateYear'))
#             grad_y = int(form_data.get('graduationYear'))
#             if not (1950 < ssc_y < int_y < grad_y < 2030): # Added more robust range check
#                  error="Education years must be valid, sequential (SSC < Intermediate < Graduation), and within reasonable limits."
#                  logging.error(error + f" Years: {ssc_y}, {int_y}, {grad_y}")
#                  return render_template('form.html', error=error, form_data=form_data_repop)
#         except (ValueError, TypeError, KeyError): # Catch potential errors
#              error = "Education years must be provided as valid numbers."
#              logging.error(error + f" Data: {form_data}")
#              return render_template('form.html', error=error, form_data=form_data_repop)


#         # --- Save to MongoDB ---
#         try:
#             # Insert the validated and processed form data
#             result = applications_collection.insert_one(form_data)
#             inserted_id = result.inserted_id
#             logging.info(f"Form data saved to MongoDB. Inserted ID: {inserted_id}")
#             # Use flash messages for success indication on the next request (redirect)
#             flash('Form submitted successfully! Thank you for your application.', 'success')
#             return redirect(url_for('success', id=str(inserted_id)))

#         except pymongo.errors.DuplicateKeyError as e:
#              logging.error(f"Duplicate key error: {e}")
#              # Check if the error is related to email if you have a unique index on it
#              if 'email' in str(e):
#                  error = "This email address has already been submitted. Please use a different email."
#              else:
#                  error = "There was a conflict saving the data (duplicate entry). Please check your input."
#              return render_template('form.html', error=error, form_data=form_data_repop)

#         except pymongo.errors.ConnectionFailure as e:
#             logging.exception("MongoDB connection failure during insert")
#             error = "Failed to connect to the database. Please try again later."
#             return render_template('form.html', error=error, form_data=form_data_repop)
#         except Exception as e:
#             logging.exception("An unexpected error occurred saving to MongoDB")
#             error = f"An unexpected error occurred while saving your application: {e}"
#             return render_template('form.html', error=error, form_data=form_data_repop)

#     # For GET request or if POST had issues before redirection
#     # Pass None for form_data on initial GET, pass repop data if coming from POST error
#     initial_form_data = None if request.method == 'GET' else form_data_repop
#     return render_template('form.html', error=error, form_data=initial_form_data) # PASSES None ON GET

# @app.route('/success')
# def success():
#     """Displays the success page and potentially the submitted data."""
#     inserted_id_str = request.args.get('id')
#     logging.debug(f"Accessing success page for ID: {inserted_id_str}")
#     application_data = None
#     error = None # Error specific to fetching data for success page

#     if not inserted_id_str:
#         logging.warning("No ID provided in success route query string")
#         # Don't set an error here, flash message from redirect should show success
#         # Optionally, you could flash a warning here if ID is expected
#     else:
#         # ***** FIX APPLIED HERE *****
#         if applications_collection is None:
#              logging.error("Database collection is not available for success page.")
#              error = "Database connection error. Cannot retrieve submission details."
#         else:
#             try:
#                 # Validate ObjectId format before querying
#                 if not ObjectId.is_valid(inserted_id_str):
#                      logging.warning(f"Invalid ObjectId format received: {inserted_id_str}")
#                      error = "Invalid application reference ID format."
#                 else:
#                     application_data = applications_collection.find_one({"_id": ObjectId(inserted_id_str)})
#                     if application_data:
#                         logging.debug(f"Application data found for success page: {application_data}")
#                     else:
#                         logging.warning(f"Application not found with ID: {inserted_id_str}")
#                         error = "Error: Submitted application data could not be found using the provided reference."
#             except Exception as e:
#                 logging.exception(f"Error fetching data from MongoDB for ID {inserted_id_str}")
#                 error = "An error occurred while retrieving submission details from the database."

#     # Render success template, passing application data and any fetching error message
#     # The main success message comes from the flash message set during redirect
#     return render_template('success.html', application=application_data, error=error)

# # --- Main Execution ---
# if __name__ == '__main__':
#     # Set host='0.0.0.0' to make it accessible on your network if needed
#     # Set port as needed, default is 5000
#     app.run(debug=True, host='0.0.0.0', port=5001) # Using port 5001 as example
