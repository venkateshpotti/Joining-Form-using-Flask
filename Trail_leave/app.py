import os
from flask import Flask, render_template, request, jsonify
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure
from dotenv import load_dotenv
from bson import ObjectId # To handle MongoDB ObjectIds
from datetime import datetime, timezone # To handle dates correctly
import re # For potential regex validation

# Load environment variables from .env file
load_dotenv()

# --- Flask App Initialization ---
app = Flask(__name__)

# --- MongoDB Connection ---
MONGO_URI = os.getenv("MONGO_URI")
DATABASE_NAME = os.getenv("DATABASE_NAME", "leave_management")

if not MONGO_URI:
    print("❌ ERROR: MONGO_URI environment variable not set.")
    # Consider exiting or using a default local connection
    # exit(1)

client = None
db = None
leaves_collection = None

try:
    print(f"⏳ Attempting to connect to MongoDB...")
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.admin.command('ping') # Check connection
    print("✅ MongoDB connection successful!")
    db = client[DATABASE_NAME]
    leaves_collection = db["applications"] # Collection name
    print(f"   Using database: '{DATABASE_NAME}'")
    print(f"   Using collection: '{leaves_collection.name}'")
    # Optional: Create indexes for faster querying
    # leaves_collection.create_index([("empId", 1)])
    # leaves_collection.create_index([("submittedAt", -1)])
except ConnectionFailure as ce:
    print(f"❌ MongoDB Connection Failure: {ce}")
except Exception as e:
    print(f"❌ An error occurred during MongoDB setup: {e}")

# --- Helper Function to Serialize MongoDB Docs ---
def serialize_doc(doc):
    """Converts MongoDB document to JSON-serializable dict."""
    if doc is None: return None
    serialized = {}
    for key, value in doc.items():
        if isinstance(value, ObjectId):
            serialized['id'] = str(value) # Use 'id' for frontend consistency
        elif isinstance(value, datetime):
            # Ensure timezone is UTC before formatting
            if value.tzinfo is None:
                 value = value.replace(tzinfo=timezone.utc)
            else:
                 value = value.astimezone(timezone.utc)
            serialized[key] = value.strftime('%Y-%m-%dT%H:%M:%S.%fZ') # ISO format Z indicates UTC
        else:
            # Keep _id as string 'id' if it wasn't an ObjectId somehow
            if key == '_id':
                 serialized['id'] = str(value)
            else:
                 serialized[key] = value

    # Ensure 'id' field exists if '_id' was present
    if '_id' in doc and 'id' not in serialized:
         serialized['id'] = str(doc['_id'])

    # Remove original '_id' if it exists and we added 'id'
    if '_id' in serialized:
        del serialized['_id']

    return serialized

# --- Flask Routes ---

@app.route('/')
def index():
    """Serves the main HTML page."""
    return render_template('index.html')

@app.route('/api/leaves', methods=['GET'])
def get_leaves():
    """API: Fetch all leave applications."""
    if leaves_collection is None:
        return jsonify({"error": "Database not available"}), 503 # Service Unavailable
    try:
        # Sort by submission date descending (newest first)
        leaves_cursor = leaves_collection.find().sort("submittedAt", -1)
        leaves_list = [serialize_doc(leave) for leave in leaves_cursor]
        return jsonify(leaves_list)
    except Exception as e:
        print(f"❌ Error fetching leaves: {e}")
        return jsonify({"error": "Failed to fetch leave data"}), 500

@app.route('/api/leaves', methods=['POST'])
def submit_leave():
    """API: Submit a new leave application."""
    if leaves_collection is None:
        return jsonify({"error": "Database not available"}), 503

    if not request.is_json:
        return jsonify({"error": "Invalid request format: JSON required"}), 400

    data = request.get_json()

    # --- Server-Side Validation ---
    required_fields = ['name', 'empId', 'email', 'leaveType', 'fromDate', 'toDate', 'reason']
    errors = {}

    for field in required_fields:
        # Use .get() with default empty string to avoid KeyError if field missing entirely
        if not data.get(field, '').strip():
            # Create user-friendly field names
            field_name = field.replace('Type', ' type').replace('Date', ' date')
            field_name = field_name[0].upper() + field_name[1:]
            errors[field] = f"{field_name} is required."

    # Specific format/length validations (should mirror frontend logic)
    if 'name' not in errors and not (3 <= len(data.get('name', '')) <= 50):
         errors['name'] = "Name must be 3-50 characters."
    # Employee ID validation (matches original JS pattern)
    if 'empId' not in errors and not re.match(r"^[A-Z]{3}0[0-9]{3}$", data.get('empId', '')):
        errors['empId'] = "Employee ID format: 3 Caps, '0', 3 digits (e.g., ABC0123)."
    # Email validation (matches original JS pattern)
    if 'email' not in errors and not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", data.get('email', '')):
        errors['email'] = "Invalid email format."
    # Reason length (matches original JS min/maxlength)
    if 'reason' not in errors and not (5 <= len(data.get('reason', '')) <= 100): # Max 100 from original textarea
         errors['reason'] = "Reason must be 5-100 characters."

    # Date logic validation
    from_dt, to_dt = None, None # Initialize
    try:
        from_date_str = data.get('fromDate')
        to_date_str = data.get('toDate')
        if 'fromDate' not in errors and from_date_str:
             from_dt = datetime.strptime(from_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if 'toDate' not in errors and to_date_str:
             to_dt = datetime.strptime(to_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)

        if from_dt and to_dt and to_dt < from_dt:
             errors['toDate'] = "To Date cannot be before From Date."

        # Optional: Validate against being too far in past/future (mirroring JS)
        # today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        # six_months_later = (datetime.now(timezone.utc) + timedelta(days=180)).replace(hour=23, minute=59, second=59)
        # if from_dt and (from_dt < today or from_dt > six_months_later):
        #     errors['fromDate'] = "From Date must be today or within the next 6 months."

    except (ValueError, TypeError) as date_err:
        print(f"Date parsing error: {date_err}")
        if 'fromDate' not in errors and not from_dt: errors['fromDate'] = "Invalid From Date."
        if 'toDate' not in errors and not to_dt: errors['toDate'] = "Invalid To Date."

    # Hour validation (only if dates are valid and the same)
    if from_dt and to_dt and from_dt == to_dt:
        from_h, to_h = data.get('fromHour'), data.get('toHour')
        if from_h and to_h: # Only validate if both are provided for a single day
            try:
                t1 = datetime.strptime(from_h, '%H:%M').time()
                t2 = datetime.strptime(to_h, '%H:%M').time()
                if t1 >= t2:
                     errors['fromHour'] = errors.get('fromHour',"From Hour must be before To Hour.") # Keep existing error if any
                # Check min duration (30 mins)
                dt1 = datetime.combine(datetime.min.date(), t1)
                dt2 = datetime.combine(datetime.min.date(), t2)
                if (dt2 - dt1).total_seconds() < 30 * 60:
                     errors['toHour'] = errors.get('toHour',"Minimum duration is 30 minutes.")
            except ValueError:
                 # Don't overwrite existing 'fromHour' error if 'toHour' is invalid format
                 if 'fromHour' not in errors: errors['fromHour'] = "Invalid time format."
                 if 'toHour' not in errors: errors['toHour'] = "Invalid time format."
    else:
        # Ensure hours are nullified if dates are different
        data['fromHour'] = None
        data['toHour'] = None


    if errors:
        # Return specific validation errors
        return jsonify({"error": "Validation failed", "details": errors}), 400

    # --- Passed Validation ---
    try:
        # --- Prepare data for MongoDB ---
        new_leave = {
            "name": data["name"].strip(),
            "empId": data["empId"].strip(),
            "email": data["email"].strip().lower(),
            "leaveType": data["leaveType"],
            "fromDate": from_dt, # Use validated UTC datetime
            "toDate": to_dt,     # Use validated UTC datetime
            "fromHour": data.get("fromHour"), # Will be None if dates differ or not provided
            "toHour": data.get("toHour"),
            "reason": data["reason"].strip(),
            "status": "Pending",
            "submittedAt": datetime.now(timezone.utc)
        }

        # --- Overlap Check ---
        overlap_query = {
            "status": "Approved",
            "empId": new_leave["empId"],
            "$or": [
                # Date ranges overlap: (StartA <= EndB) and (EndA >= StartB)
                {"fromDate": {"$lte": new_leave["toDate"]}, "toDate": {"$gte": new_leave["fromDate"]}}
            ]
        }
        # Use count_documents for efficiency if just checking existence
        if leaves_collection.count_documents(overlap_query) > 0:
             return jsonify({"error": "Leave request overlaps with an existing approved leave."}), 409

        # --- Insert into MongoDB ---
        result = leaves_collection.insert_one(new_leave)

        # Return success response
        return jsonify({
            "message": "Leave submitted successfully!",
            "id": str(result.inserted_id) # Send back the ID
        }), 201

    except OperationFailure as ofe:
         print(f"❌ MongoDB Operation Failure during POST: {ofe}")
         return jsonify({"error": "Database error during submission"}), 500
    except Exception as e:
        print(f"❌ Error submitting leave: {e}")
        # Log the full error traceback here for debugging
        return jsonify({"error": "An internal server error occurred"}), 500

# --- Error Handlers ---
@app.errorhandler(404)
def page_not_found(e):
    if request.path.startswith('/api/'):
        return jsonify(error="API endpoint not found"), 404
    return render_template('index.html'), 404 # Route non-API 404s to the app

@app.errorhandler(500)
def internal_server_error(e):
    print(f"!! SERVER ERROR 500: {e}")
    if request.path.startswith('/api/'):
        return jsonify(error="Internal server error"), 500
    return "<h1>500 - Internal Server Error</h1>", 500

# --- Main Execution ---
if __name__ == '__main__':
    print(f"🚀 Starting Flask server...")
    app.run(debug=True, host='0.0.0.0', port=5000)