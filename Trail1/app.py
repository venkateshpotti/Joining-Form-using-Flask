import os
import logging
import pymongo
from flask import Flask, render_template, request, redirect, url_for, flash
from pymongo import MongoClient
from bson import ObjectId
from dotenv import load_dotenv
from werkzeug.utils import secure_filename

# Load environment variables from .env file
load_dotenv()

# Initialize Flask App
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'a_default_secret_key_for_dev') # Use env var or default

# --- Configuration ---
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'pdf'}
MONGO_URI = os.environ.get('MONGO_URI')
DATABASE_NAME = "job_application_db"

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Ensure the upload folder exists
if not os.path.exists(UPLOAD_FOLDER):
    try:
        os.makedirs(UPLOAD_FOLDER)
        logging.info(f"Created upload folder: {UPLOAD_FOLDER}")
    except OSError as e:
        logging.error(f"Error creating upload folder {UPLOAD_FOLDER}: {e}")
        # Decide if the app can run without the upload folder
        # exit() or handle appropriately

# Configure Logging
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# --- MongoDB Connection ---
db = None
applications_collection = None # Initialize as None
if not MONGO_URI:
    logging.error("MONGO_URI not found in environment variables. Database functionality will be disabled.")
else:
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000) # Add timeout
        # The ismaster command is cheap and does not require auth. Confirms connection.
        client.admin.command('ismaster')
        db = client[DATABASE_NAME]
        applications_collection = db["applications"] # Assign the collection object here
        # Optional: Create a unique index on email if you want to prevent duplicates server-side
        # applications_collection.create_index([("email", pymongo.ASCENDING)], unique=True)
        logging.info("Connected to MongoDB successfully!")
    except pymongo.errors.ConnectionFailure as e:
        logging.error(f"Could not connect to MongoDB at {MONGO_URI}: {e}")
        # applications_collection remains None
    except Exception as e:
        logging.error(f"An error occurred during MongoDB setup: {e}")
        # applications_collection remains None

# --- Helper Function ---
def allowed_file(filename):
    """Checks if the filename has an allowed extension."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- Routes ---
@app.route('/', methods=['GET', 'POST'])
def job_application_form():
    """Handles the job application form display (GET) and submission (POST)."""
    logging.debug(f"Request method: {request.method}")
    error = None # Initialize error message
    form_data_repop = request.form.to_dict() # Use this for repopulating on error

    if request.method == 'POST':
        logging.debug("Received POST request")

        # Check if database connection is available
        # ***** FIX APPLIED HERE *****
        if applications_collection is None:
            logging.error("Database collection is not available.")
            error = "Database connection error. Please try again later."
            # Pass back submitted data for repopulation
            return render_template('form.html', error=error, form_data=form_data_repop)

        form_data = request.form.to_dict()
        logging.debug(f"Form data received: {form_data}")
        logging.debug(f"Files received: {request.files}")

        # --- File Handling ---
        files_to_process = {
            'sscDoc': request.files.get('sscDoc'),
            'intermediateDoc': request.files.get('intermediateDoc'),
            'graduationDoc': request.files.get('graduationDoc'),
            'Additionalfiles': request.files.get('Additionalfiles') # Optional file
        }

        file_paths = {}
        validation_error_occurred = False

        for field, file_storage in files_to_process.items():
            is_required = field != 'Additionalfiles' # Additionalfiles is optional

            if file_storage and file_storage.filename: # Check if FileStorage exists and has a filename
                if allowed_file(file_storage.filename):
                    filename = secure_filename(file_storage.filename)
                    # Consider making filenames unique to prevent overwrites
                    unique_filename = f"{ObjectId()}_{filename}"
                    file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                    try:
                        file_storage.save(file_path)
                        # Store the path relative to the app or a designated access point if needed later
                        # For simplicity, storing the saved path here. Might store just unique_filename.
                        file_paths[field] = file_path
                        logging.debug(f"Saved file {field} to {file_path}")
                    except Exception as e:
                         logging.error(f"Error saving file {field} ('{filename}'): {e}")
                         error = f"Could not save file: {filename}. Please ensure the server has permission and disk space."
                         validation_error_occurred = True
                         break # Stop processing further files on save error
                else:
                    logging.error(f"Invalid file type for {field}: {file_storage.filename}")
                    # Capitalize field name nicely for error message
                    field_name_display = field.replace('Doc',' Document').replace('files', ' Files')
                    error = f"{field_name_display}: Only PDF files are allowed."
                    validation_error_occurred = True
                    break # Stop processing on invalid file type
            elif is_required:
                # File is required but not provided or filename is empty
                logging.error(f"Missing required file: {field}")
                field_name_display = field.replace('Doc',' Document')
                error = f"{field_name_display} is required. Please upload a PDF file."
                validation_error_occurred = True
                break # Stop processing on missing required file

        if validation_error_occurred:
             # Repopulate form with originally submitted data and show file error
            return render_template('form.html', error=error, form_data=form_data_repop)

        # Update form_data with file paths (or just filenames if preferred)
        form_data.update(file_paths) # Now form_data contains text fields + paths to saved files

        # --- Server-side Data Validation ---
        base_required_fields = [
            'department', 'jobRole', 'branchLocation', 'expectedSalary', 'interviewDate',
            'joiningDate', 'employmentType', 'FullName', 'email', 'dob', 'mobileNumber',
            'fatherName', 'permanentAddress', 'sscYear', 'sscPercentage', 'intermediateYear',
            'intermediatePercentage', 'higherGraduation', 'registerNumber', 'graduationYear',
            'graduationPercentage', 'experienceStatus'
            # File fields (sscDoc, intermediateDoc, graduationDoc) validated during file handling above
        ]

        # Add conditional experience fields if status is 'experience'
        experience_status = form_data.get('experienceStatus')
        required_fields = base_required_fields[:] # Create a copy

        if experience_status == 'experience':
            experience_required = ['yearsOfExperience', 'previousCompany', 'previousJobRole']
            required_fields.extend(experience_required)
        elif experience_status == 'fresher':
             # Remove any potentially submitted experience data if fresher (to avoid saving inconsistent data)
            form_data.pop('yearsOfExperience', None)
            form_data.pop('previousCompany', None)
            form_data.pop('previousJobRole', None)
        else:
            # Handle case where experienceStatus might be missing (shouldn't happen with 'required' in HTML)
            error = "Experience Status is required."
            logging.error(error + f" Data: {form_data}")
            return render_template('form.html', error=error, form_data=form_data_repop)


        missing_fields = []
        for field in required_fields:
            # Check if field exists and has a non-empty value after stripping whitespace
            value = form_data.get(field)
            if value is None or not str(value).strip():
                 # Simple capitalization/spacing for display - adapt as needed
                 field_display = field.replace('_', ' ').replace('Doc', ' Document').title()
                 missing_fields.append(field_display)

        if missing_fields:
            error = f"Missing required fields: {', '.join(missing_fields)}."
            logging.error(f"Validation Error: {error} Data: {form_data}")
            return render_template('form.html', error=error, form_data=form_data_repop)

        # --- Data Type/Format Validation (Examples) ---
        try:
            salary = int(form_data.get('expectedSalary', 0))
            if salary < 100000:
                error="Expected Salary must be at least 100,000."
                logging.error(error + f" Value: {form_data.get('expectedSalary')}")
                return render_template('form.html', error=error, form_data=form_data_repop)
        except (ValueError, TypeError):
            error="Expected Salary must be a valid number."
            logging.error(error + f" Value: {form_data.get('expectedSalary')}")
            return render_template('form.html', error=error, form_data=form_data_repop)

        # Basic Email Format Check (consider using a library like 'email_validator' for robust check)
        email = form_data.get('email', '')
        if '@' not in email or '.' not in email.split('@')[-1] or len(email.split('@')) > 2:
             error="Invalid email format provided."
             logging.error(error + f" Value: {email}")
             return render_template('form.html', error=error, form_data=form_data_repop)

        # Add more specific validations here (phone format, date ranges, year sequences, etc.)
        # Example: Check year sequence basic
        try:
            ssc_y = int(form_data.get('sscYear'))
            int_y = int(form_data.get('intermediateYear'))
            grad_y = int(form_data.get('graduationYear'))
            if not (1950 < ssc_y < int_y < grad_y < 2030): # Added more robust range check
                 error="Education years must be valid, sequential (SSC < Intermediate < Graduation), and within reasonable limits."
                 logging.error(error + f" Years: {ssc_y}, {int_y}, {grad_y}")
                 return render_template('form.html', error=error, form_data=form_data_repop)
        except (ValueError, TypeError, KeyError): # Catch potential errors
             error = "Education years must be provided as valid numbers."
             logging.error(error + f" Data: {form_data}")
             return render_template('form.html', error=error, form_data=form_data_repop)


        # --- Save to MongoDB ---
        try:
            # Insert the validated and processed form data
            result = applications_collection.insert_one(form_data)
            inserted_id = result.inserted_id
            logging.info(f"Form data saved to MongoDB. Inserted ID: {inserted_id}")
            # Use flash messages for success indication on the next request (redirect)
            flash('Form submitted successfully! Thank you for your application.', 'success')
            return redirect(url_for('success', id=str(inserted_id)))

        except pymongo.errors.DuplicateKeyError as e:
             logging.error(f"Duplicate key error: {e}")
             # Check if the error is related to email if you have a unique index on it
             if 'email' in str(e):
                 error = "This email address has already been submitted. Please use a different email."
             else:
                 error = "There was a conflict saving the data (duplicate entry). Please check your input."
             return render_template('form.html', error=error, form_data=form_data_repop)

        except pymongo.errors.ConnectionFailure as e:
            logging.exception("MongoDB connection failure during insert")
            error = "Failed to connect to the database. Please try again later."
            return render_template('form.html', error=error, form_data=form_data_repop)
        except Exception as e:
            logging.exception("An unexpected error occurred saving to MongoDB")
            error = f"An unexpected error occurred while saving your application: {e}"
            return render_template('form.html', error=error, form_data=form_data_repop)

    # For GET request or if POST had issues before redirection
    # Pass None for form_data on initial GET, pass repop data if coming from POST error
    initial_form_data = None if request.method == 'GET' else form_data_repop
    return render_template('form.html', error=error, form_data=initial_form_data) # PASSES None ON GET

@app.route('/success')
def success():
    """Displays the success page and potentially the submitted data."""
    inserted_id_str = request.args.get('id')
    logging.debug(f"Accessing success page for ID: {inserted_id_str}")
    application_data = None
    error = None # Error specific to fetching data for success page

    if not inserted_id_str:
        logging.warning("No ID provided in success route query string")
        # Don't set an error here, flash message from redirect should show success
        # Optionally, you could flash a warning here if ID is expected
    else:
        # ***** FIX APPLIED HERE *****
        if applications_collection is None:
             logging.error("Database collection is not available for success page.")
             error = "Database connection error. Cannot retrieve submission details."
        else:
            try:
                # Validate ObjectId format before querying
                if not ObjectId.is_valid(inserted_id_str):
                     logging.warning(f"Invalid ObjectId format received: {inserted_id_str}")
                     error = "Invalid application reference ID format."
                else:
                    application_data = applications_collection.find_one({"_id": ObjectId(inserted_id_str)})
                    if application_data:
                        logging.debug(f"Application data found for success page: {application_data}")
                    else:
                        logging.warning(f"Application not found with ID: {inserted_id_str}")
                        error = "Error: Submitted application data could not be found using the provided reference."
            except Exception as e:
                logging.exception(f"Error fetching data from MongoDB for ID {inserted_id_str}")
                error = "An error occurred while retrieving submission details from the database."

    # Render success template, passing application data and any fetching error message
    # The main success message comes from the flash message set during redirect
    return render_template('success.html', application=application_data, error=error)

# --- Main Execution ---
if __name__ == '__main__':
    # Set host='0.0.0.0' to make it accessible on your network if needed
    # Set port as needed, default is 5000
    app.run(debug=True, host='0.0.0.0', port=5001) # Using port 5001 as example