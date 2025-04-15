import os
import datetime
import re
from flask import (
    Flask, render_template, request, redirect, url_for, flash, send_from_directory
)
from pymongo import MongoClient
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from bson import ObjectId

load_dotenv()

MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017/work_allocation_db')
SECRET_KEY = os.getenv('FLASK_SECRET_KEY', 'default_secret_key')
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx', 'jpg', 'jpeg', 'png', 'txt'}

app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

client = None
db = None
tasks_collection = None
history_collection = None

try:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000) # Added timeout
    # The ismaster command is cheap and does not require auth.
    client.admin.command('ismaster')
    db = client.get_database() # Get DB name from URI or specify if needed
    tasks_collection = db.tasks
    history_collection = db.history
    print("Successfully connected to MongoDB!")
except Exception as e:
    print(f"Error connecting to MongoDB: {e}")
    # Ensure these are None if connection failed
    client = None
    db = None
    tasks_collection = None
    history_collection = None


def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_tasks():
    # Check explicitly for None
    if client is None or tasks_collection is None:
        print("DB connection not available for get_tasks")
        return []
    try:
        tasks = list(tasks_collection.find().sort("allocatedTime", -1))
        return tasks
    except Exception as e:
        print(f"Error fetching tasks: {e}")
        return []

def get_history():
    # Check explicitly for None
    if client is None or history_collection is None:
        print("DB connection not available for get_history")
        return []
    try:
        history = list(history_collection.find().sort("submittedTime", -1))
        return history
    except Exception as e:
        print(f"Error fetching history: {e}")
        return []


@app.route('/')
def index():
    allocated_tasks = get_tasks()
    work_history = get_history()
    return render_template('index.html', tasks=allocated_tasks, history=work_history)

@app.route('/submit', methods=['POST'])
def submit_work():
    # Check explicitly for None
    if client is None or history_collection is None:
        flash('Database connection error. Cannot submit work.', 'danger')
        return redirect(url_for('index'))

    task_name = request.form.get('task-name')
    employee_name = request.form.get('employee-name')
    employee_id = request.form.get('employee-id')
    task_status = request.form.get('task-status')

    errors = []
    if not task_name or len(task_name) < 5:
        errors.append("Work Item name must be at least 5 characters.")
    if not employee_name or not employee_name.strip():
        errors.append("Employee Name is required.")

    emp_id_pattern = r"^[A-Z]{3}(?!0000)[0-9]{4}$"
    if not employee_id or not re.match(emp_id_pattern, employee_id):
         errors.append("Employee ID must be in the format ABC1234 (and not ABC0000).")
    if not task_status:
        errors.append("Work Status is required.")

    uploaded_filename = None
    if 'upload-doc' not in request.files:
        errors.append('No document file part in the request.')
    else:
        file = request.files['upload-doc']
        if file.filename == '':
            errors.append('No document selected for upload.')
        elif file and allowed_file(file.filename):
            try:
                original_filename = secure_filename(file.filename)
                timestamp = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")
                # Consider adding a random element for very high concurrency
                unique_filename = f"{timestamp}_{original_filename}"
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                file.save(file_path)
                uploaded_filename = unique_filename
                print(f"File saved: {file_path}")
            except Exception as e:
                print(f"Error saving file: {e}")
                errors.append(f"Could not save the uploaded file: {e}")
        elif file and not allowed_file(file.filename):
             errors.append('Invalid file type. Allowed types: pdf, doc, docx, jpg, jpeg, png, txt')

    if errors:
        for error in errors:
            flash(error, 'danger')
        # Redirect back to the form, potentially passing old data if needed
        return redirect(url_for('index'))
    else:
        try:
            history_entry = {
                "taskName": task_name,
                "employeeName": employee_name,
                "employeeId": employee_id,
                "uploadDocFilename": uploaded_filename,
                "taskStatus": task_status,
                "submittedTime": datetime.datetime.utcnow()
            }
            history_collection.insert_one(history_entry)
            flash('Work update submitted successfully!', 'success')
        except Exception as e:
            print(f"Error saving history to MongoDB: {e}")
            flash(f'Error saving submission: {e}', 'danger')

        return redirect(url_for('index'))

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    # Basic security check - don't allow path traversal
    if '..' in filename or filename.startswith('/'):
        flash('Invalid filename.', 'danger')
        return redirect(url_for('index')) # Or return 404

    try:
        # Ensure the directory being served from is exactly UPLOAD_FOLDER
        safe_directory = os.path.abspath(app.config['UPLOAD_FOLDER'])
        return send_from_directory(safe_directory, filename, as_attachment=False) # as_attachment=True forces download
    except FileNotFoundError:
        flash('File not found.', 'warning')
        return redirect(url_for('index')) # Or render a 404 page


if __name__ == '__main__':
    # --- THIS IS THE CORRECTED LINE ---
    if client is not None and tasks_collection is not None and tasks_collection.count_documents({}) == 0:
        print("Adding sample task data...")
        sample_tasks = [
            {"taskName": "Implement User Login", "project": "HRMS Portal", "allocatedTime": "2023-10-26 09:00", "deadline": "2023-11-05 17:00", "status": "pending"},
            {"taskName": "Design Dashboard UI", "project": "HRMS Portal", "allocatedTime": "2023-10-25 10:00", "deadline": "2023-11-02 17:00", "status": "pending"},
            {"taskName": "Setup Database Schema", "project": "HRMS Portal", "allocatedTime": "2023-10-24 14:00", "deadline": "2023-10-30 17:00", "status": "inprocess"}
        ]
        try:
            tasks_collection.insert_many(sample_tasks)
            print("Sample task data added.")
        except Exception as e:
            print(f"Error adding sample task data: {e}")
    # Add sample history data check if needed
    # if client is not None and history_collection is not None and history_collection.count_documents({}) == 0:
    #    print("Adding sample history data...")
    #    # ... add sample history ...

    app_debug = os.getenv('FLASK_DEBUG', 'False').lower() in ('true', '1', 't')
    # Use 0.0.0.0 to make it accessible on your network, or 127.0.0.1 for local only
    app.run(debug=app_debug, host='0.0.0.0', port=5005)