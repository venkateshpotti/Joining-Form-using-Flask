# app.py
import os
from flask import Flask, render_template, request, jsonify
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure
from datetime import datetime
import logging # Optional: for better logging

# --- Configuration ---
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "wfh_requests_db"
COLLECTION_NAME = "requests"

# --- Initialize Flask App ---
app = Flask(__name__)

# --- Setup Logging (Optional but Recommended) ---
logging.basicConfig(level=logging.INFO)

# --- MongoDB Connection ---
try:
    client = MongoClient(MONGO_URI)
    client.admin.command('ismaster') # Check connection
    db = client[DB_NAME]
    requests_collection = db[COLLECTION_NAME]
    logging.info("Successfully connected to MongoDB.")
except ConnectionFailure:
    logging.error("MongoDB connection failed. Check if MongoDB server is running or URI is correct.")
    client = None
    requests_collection = None # Set to None on failure
except Exception as e:
    logging.error(f"An error occurred during MongoDB setup: {e}")
    client = None
    requests_collection = None # Set to None on failure

# --- Routes ---
@app.route('/')
def index():
    """
    Serves the main index page. Fetches existing WFH requests from MongoDB
    and passes them to the template for initial rendering.
    """
    # CORRECT CHECK: Compare with None explicitly
    if requests_collection is None:
         # Handle case where DB connection failed during startup
        return render_template('index.html', requests=[], error="Database connection failed.")

    try:
        all_requests = list(requests_collection.find().sort("submittedAt", -1))
        logging.info(f"Fetched {len(all_requests)} requests from DB.")
        return render_template('index.html', requests=all_requests)
    except Exception as e:
        logging.error(f"Error fetching requests from MongoDB: {e}")
        return render_template('index.html', requests=[], error="Could not fetch requests.")


@app.route('/submit_request', methods=['POST'])
def submit_request():
    """
    Handles the form submission via POST request.
    Receives JSON data, validates (basic), adds metadata,
    and inserts into the MongoDB collection.
    """
    # CORRECT CHECK: Compare with None explicitly
    if requests_collection is None:
        return jsonify({"success": False, "message": "Database not available."}), 500

    if not request.is_json:
        return jsonify({"success": False, "message": "Request must be JSON."}), 400

    data = request.get_json()
    logging.info(f"Received submission data: {data}")

    required_fields = ['name', 'id', 'email', 'project', 'manager', 'location', 'from', 'to', 'reason']
    missing_fields = [field for field in required_fields if field not in data or not data[field]]

    if missing_fields:
        logging.warning(f"Submission failed: Missing fields - {missing_fields}")
        return jsonify({
            "success": False,
            "message": f"Missing required fields: {', '.join(missing_fields)}"
        }), 400

    try:
        request_document = {
            "reqId": data.get("reqId", f"req_{int(datetime.now().timestamp()*1000)}"),
            "name": data['name'],
            "employeeId": data['id'],
            "email": data['email'],
            "project": data['project'],
            "manager": data['manager'],
            "location": data['location'],
            "fromDate": data['from'],
            "toDate": data['to'],
            "reason": data['reason'],
            "status": "Pending",
            "submittedAt": datetime.utcnow()
        }

        insert_result = requests_collection.insert_one(request_document)
        logging.info(f"Successfully inserted request with ID: {insert_result.inserted_id}")

        response_data = request_document.copy()
        response_data['_id'] = str(insert_result.inserted_id)
        response_data['submittedAt'] = request_document['submittedAt'].isoformat() + "Z"

        return jsonify({
            "success": True,
            "message": "Request submitted successfully!",
            "request": response_data
            }), 201

    except Exception as e:
        logging.error(f"Error inserting request into MongoDB: {e}")
        return jsonify({"success": False, "message": "An internal error occurred."}), 500

# --- Run the App ---
if __name__ == '__main__':
    app.run(debug=True) # Removed host/port for simplicity, add back if needed