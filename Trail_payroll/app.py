import os
import re # Make sure re is imported
import datetime
import io
from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, send_file, session
)
from dotenv import load_dotenv
from pymongo import MongoClient, errors as mongoerrors

load_dotenv()

app = Flask(__name__)
# IMPORTANT: Set a strong secret key in your .env file or environment variables
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'a_default_insecure_secret_key_change_me')

# --- MongoDB Configuration ---
MONGO_URI = os.getenv('MONGO_URI')
client = None
db = None
payslip_requests_collection = None

if not MONGO_URI:
    print("\n" + "="*50)
    print("WARNING: MONGO_URI not set in environment variables or .env file.")
    print("Database features will be disabled.")
    print("Create a .env file with MONGO_URI='your_connection_string'")
    print("="*50 + "\n")
else:
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000) # 5 second timeout
        client.admin.command('ismaster') # Check connection
        db = client.get_database() # Gets default DB from URI
        if not db.name: # Should typically not happen if URI is valid
             raise mongoerrors.ConfigurationError("Database name not found in MONGO_URI or is invalid.")
        payslip_requests_collection = db.payslip_requests # Collection name
        print(f"Successfully connected to MongoDB! Database: '{db.name}', Collection: '{payslip_requests_collection.name}'")
    except mongoerrors.ConfigurationError as e:
        print(f"MongoDB Configuration Error (check your MONGO_URI format): {e}")
        client = None
    except mongoerrors.ConnectionFailure as e:
        print(f"Error connecting to MongoDB (is it running? firewall?): {e}")
        client = None
    except Exception as e:
        print(f"An unexpected error occurred during MongoDB initialization: {e}")
        client = None

# Ensure db and collection are None if client is None
if client is None:
    db = None
    payslip_requests_collection = None


# --- Validation Patterns ---
EMP_ID_PATTERN = re.compile(r"^[A-Z]{3}0(?!000)[0-9]{3}$")
NAME_PATTERN = re.compile(r"^[A-Za-z]+(?:\.[A-Za-z]+)*(?: [A-Za-z]+)*(?:\.[A-Za-z]+){0,3}$")
EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
PASSWORD_PATTERN = re.compile(r"^(?=.*[A-Za-z])(?=.*[0-9])(?=.*[\W_]).{5,}$")
# ** Stricter Regex for YYYY-MM format (strictly digits) **
MONTH_PATTERN = re.compile(r"^\d{4}-\d{2}$")

# --- Helper Functions ---
def validate_date_logic(start_month_str, end_month_str):
    """Validates start and end month logic, ensuring correct input format."""
    # --- Add Debug Prints ---
    print(f"DEBUG: validate_date_logic received: start='{start_month_str}', end='{end_month_str}'")
    # -----------------------

    errors = []
    today = datetime.date.today()
    current_month_start = today.replace(day=1)
    start_month_date = None
    end_month_date = None

    # --- Validate Start Month ---
    try:
        if not start_month_str:
            # This case should ideally be caught by 'required' but good to have
            raise ValueError("Start month is required.")

        # ** STRICTER FORMAT CHECK **
        if not MONTH_PATTERN.match(start_month_str):
            # Provide more context in the error
            raise ValueError(f"Expected format YYYY-MM, but received '{start_month_str}'. Please re-select the month.")

        # Construct the date string *after* format validation
        constructed_date_str = f"{start_month_str}-01"
        print(f"DEBUG: Attempting to parse start month as: '{constructed_date_str}' with format '%Y-%m-%d'") # Debug print
        start_month_date = datetime.datetime.strptime(constructed_date_str, '%Y-%m-%d').date()

        # Check if date is in the future or before minimum year
        if start_month_date > current_month_start:
            errors.append("Start month cannot be in the future.")
        if start_month_date.year < 2022: # Example minimum year
            errors.append("Start month must be from January 2022 onwards.")

    except ValueError as e:
        # Catch errors from format check or strptime
        errors.append(f"Invalid start month: {e}")
        # No need to return early, collect all errors

    # --- Validate End Month (only if provided) ---
    if end_month_str: # Only process if a value exists
        try:
            # ** STRICTER FORMAT CHECK **
            if not MONTH_PATTERN.match(end_month_str):
                 raise ValueError(f"Expected format YYYY-MM, but received '{end_month_str}'. Please re-select the month.")

            # Construct the date string *after* format validation
            constructed_date_str = f"{end_month_str}-01"
            print(f"DEBUG: Attempting to parse end month as: '{constructed_date_str}' with format '%Y-%m-%d'") # Debug print
            end_month_date = datetime.datetime.strptime(constructed_date_str, '%Y-%m-%d').date()

            # Check future date
            if end_month_date > current_month_start:
                 errors.append("End month cannot be in the future.")
            # Check minimum year
            if end_month_date.year < 2022:
                 errors.append("End month must be from January 2022 onwards.")
            # Check end relative to start (only if start_month_date is valid and not None)
            if start_month_date and end_month_date <= start_month_date:
                 errors.append("End month must be after the start month.")

        except ValueError as e:
            errors.append(f"Invalid end month: {e}")

    # Return collected errors and potentially valid dates
    return errors, start_month_date, end_month_date


# --- Routes ---
@app.route('/')
def index():
    """Displays the initial payslip form."""
    # Retrieve form data from session if it exists (for repopulation after error)
    form_data = session.get('form_data', None)
    # Optionally clear it if you want a fresh form every time index is visited directly
    # session.pop('form_data', None)
    return render_template('payslip.html', show_payslip=False, form_data=form_data)

@app.route('/generate', methods=['POST'])
def generate_payslip():
    """Handles form submission, validation, saves to DB, and displays the payslip."""
    form_data = request.form.to_dict() # Get mutable dict
    # Store submitted data in session immediately for repopulation needs
    session['form_data'] = form_data

    # Extract data (using .get is safer than direct access)
    employee_name = form_data.get('employeeName', '').strip()
    employee_id = form_data.get('employeeId', '').strip()
    email = form_data.get('email', '').strip()
    password = form_data.get('password', '') # Don't strip password usually
    start_month_str = form_data.get('startMonth', '').strip()
    end_month_str = form_data.get('endMonth', '').strip()

    errors = []

    # --- Server-Side Validation ---
    if not employee_name or not NAME_PATTERN.match(employee_name):
        errors.append("Invalid Employee Name format. Check spacing and allowed characters (letters, single periods).")
    if not employee_id or not EMP_ID_PATTERN.match(employee_id):
        errors.append("Invalid Employee ID format (e.g., ABC0123, must be 3 capital letters, '0', then 001-999).")
    if not email or not EMAIL_PATTERN.match(email):
         errors.append("Invalid Email Address format.")
    if not password or not PASSWORD_PATTERN.match(password):
         errors.append("Password must be at least 5 characters and include 1 letter, 1 number, and 1 symbol (@, #, $, etc.).")

    # *** Call the UPDATED validation function ***
    # Pass the possibly empty/None strings directly
    date_errors, start_date, end_date = validate_date_logic(start_month_str, end_month_str)
    errors.extend(date_errors)

    if errors:
        print("Validation Errors:", errors) # Debug print
        for error in errors:
            flash(error, 'danger')
        # Re-render the form with errors, using session data to repopulate
        return render_template(
            'payslip.html',
            show_payslip=False,
            form_data=session.get('form_data') # Pass back the stored submitted data
            )

    # --- If Validation Successful ---
    print("Validation Successful. Clearing form data from session.") # Debug print
    session.pop('form_data', None) # Clear form data from session on success

    # Format pay period string (start_date is guaranteed to be valid here if no errors)
    start_month_formatted = start_date.strftime('%b %Y')
    pay_period = start_month_formatted
    # Use the original validated string for DB, or None if end wasn't provided/valid
    db_end_month_str = end_month_str if end_date else None

    if end_date:
        end_month_formatted = end_date.strftime('%b %Y')
        pay_period = f"{start_month_formatted} - {end_month_formatted}"
    # else: db_end_month_str remains None

    # --- Prepare and Save Data to MongoDB ---
    db_save_error = False
    if client is not None and payslip_requests_collection is not None:
        submission_time = datetime.datetime.utcnow()
        payslip_document = {
            "employeeName": employee_name,
            "employeeId": employee_id,
            "email": email,
            "startMonth": start_month_str, # Store original YYYY-MM
            "endMonth": db_end_month_str,  # Store original YYYY-MM or None
            "calculatedPayPeriod": pay_period,
            "submissionTimestamp": submission_time
            # --- IMPORTANT: DO NOT store the password ---
        }
        try:
            result = payslip_requests_collection.insert_one(payslip_document)
            print(f"Successfully inserted payslip request with ID: {result.inserted_id}")
        except Exception as e:
            db_save_error = True
            print(f"Error inserting payslip request into MongoDB: {e}")
            flash("Error saving request data to the database. Please try again later.", "warning")
    elif MONGO_URI: # Only warn if URI was set but connection failed
        db_save_error = True
        print("Warning: MongoDB not connected. Payslip request data not saved.")
        flash("Database not connected. Request data could not be saved.", "warning")


    # --- Store data needed for download in session ---
    session['payslip_data'] = {
        'name': employee_name, 'id': employee_id, 'email': email, 'period': pay_period,
        'basic_salary': '16,250.00', 'da': '550.00', 'hra': '1,650.00',
        'wage_allowance': '120.00', 'medical_allowance': '3,000.00',
        'total_earnings': '21,670.00', 'pf': '1,800.00', 'esi': '142.00',
        'tds': '0.00', 'lwp': '0.00', 'special_deduction': '500.00',
        'total_deductions': '2,442.00', 'net_pay': '19,228.00' # Static calculation
    }

    # Render the template to show the payslip section
    return render_template(
        'payslip.html',
        show_payslip=True,
        employee_name=employee_name,
        employee_id=employee_id,
        pay_period=pay_period,
        db_error=db_save_error # Pass flag to template if needed
    )


@app.route('/download', methods=['POST'])
def download_payslip():
    """Generates and downloads the payslip data as a text file."""
    payslip_data = session.get('payslip_data')

    if not payslip_data:
        flash('Payslip data session expired or not found. Please generate the payslip again.', 'warning')
        return redirect(url_for('index'))

    # Create payslip text content
    payslip_content = f"""
------------------------------------
        PAYSLIP
------------------------------------
Employee Name: {payslip_data.get('name', 'N/A')}
Employee ID:   {payslip_data.get('id', 'N/A')}
Pay Period:    {payslip_data.get('period', 'N/A')}
Department:    Software Developer (Static Example)

------------------------------------
        EARNINGS (Example Data)
------------------------------------
Basic Salary:          ₹ {payslip_data.get('basic_salary', '0.00')}
Dearness Allowance:    ₹ {payslip_data.get('da', '0.00')}
House Rent Allowance:  ₹ {payslip_data.get('hra', '0.00')}
Wage Allowance:        ₹ {payslip_data.get('wage_allowance', '0.00')}
Medical Allowance:     ₹ {payslip_data.get('medical_allowance', '0.00')}
------------------------------------
Total Earnings:        ₹ {payslip_data.get('total_earnings', '0.00')}
------------------------------------

------------------------------------
        DEDUCTIONS (Example Data)
------------------------------------
Provident Fund:        ₹ {payslip_data.get('pf', '0.00')}
Employee State Ins.:   ₹ {payslip_data.get('esi', '0.00')}
Tax Deducted (TDS):    ₹ {payslip_data.get('tds', '0.00')}
Leave Without Pay:     ₹ {payslip_data.get('lwp', '0.00')}
Special Deduction:     ₹ {payslip_data.get('special_deduction', '0.00')}
------------------------------------
Total Deductions:      ₹ {payslip_data.get('total_deductions', '0.00')}
------------------------------------

------------------------------------
Net Payable Salary:    ₹ {payslip_data.get('net_pay', '0.00')}
------------------------------------
Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
    mem_file = io.BytesIO()
    mem_file.write(payslip_content.encode('utf-8'))
    mem_file.seek(0)

    # Generate a safe filename
    period_safe = re.sub(r'[^\w\-]+', '_', payslip_data.get('period', 'period'))
    id_safe = re.sub(r'[^\w\-]+', '_', payslip_data.get('id', 'EMP'))
    download_filename = f"Payslip_{id_safe}_{period_safe}.txt"

    return send_file(
        mem_file,
        mimetype='text/plain',
        download_name=download_filename,
        as_attachment=True
    )


# --- Run Application ---
if __name__ == '__main__':
    app_debug = os.getenv('FLASK_DEBUG', 'False').lower() in ('true', '1', 't')
    port = int(os.getenv('PORT', 5001))
    print(f"Starting Flask app on http://0.0.0.0:{port} with debug={app_debug}")
    # Use threaded=True only if necessary and understand implications, usually not needed for development
    app.run(debug=app_debug, host='0.0.0.0', port=port)