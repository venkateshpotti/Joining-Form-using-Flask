# app.py
import os
from flask import Flask, render_template, request, jsonify, abort
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure
from bson import ObjectId # Import ObjectId
from bson.errors import InvalidId # Import InvalidId for error handling
from datetime import datetime
import logging

# --- Configuration ---
# Use environment variable for connection string in production!
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "wfh_requests_db"
COLLECTION_NAME = "requests"

# --- Initialize Flask App ---
app = Flask(__name__)

# --- Setup Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- MongoDB Connection ---
requests_collection = None # Initialize to None
try:
    # Connect with a timeout
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    # The ismaster command is cheap and does not require auth.
    client.admin.command('ismaster') # Verify connection
    db = client[DB_NAME]
    requests_collection = db[COLLECTION_NAME]
    logging.info(f"Successfully connected to MongoDB: {MONGO_URI}")
except ConnectionFailure as e:
    logging.error(f"MongoDB connection failed ({MONGO_URI}): {e}")
except Exception as e:
    logging.error(f"An error occurred during MongoDB setup: {e}")
# Keep requests_collection as None if connection failed

# --- Routes ---

@app.route('/')
def index():
    """ Serves the main user form page. """
    db_connected = requests_collection is not None
    # Pass connection status to template (optional, for displaying message if needed)
    return render_template('index.html', db_connected=db_connected)

@app.route('/submit_request', methods=['POST'])
def submit_request():
    """ Handles the user form submission. """
    if requests_collection is None:
        logging.error("Submit request failed: Database not available.")
        return jsonify({"success": False, "message": "Database not available."}), 503 # Service Unavailable

    if not request.is_json:
        return jsonify({"success": False, "message": "Request must be JSON."}), 400

    data = request.get_json()
    logging.info(f"Received submission data via /submit_request: {data}")

    # Server-side validation
    required_fields = ['name', 'id', 'email', 'project', 'manager', 'location', 'from', 'to', 'reason']
    missing_fields = [field for field in required_fields if not data.get(field)]

    if missing_fields:
        logging.warning(f"Submission failed: Missing fields - {missing_fields}")
        return jsonify({"success": False, "message": f"Missing required fields: {', '.join(missing_fields)}"}), 400

    # Add specific validation checks here if needed (e.g., ID format, email format, date logic)

    try:
        request_document = {
            # Use provided ID or generate one; consider if reqId is needed or just use _id
            "reqId": data.get("reqId", f"req_{int(datetime.now().timestamp()*1000)}"),
            "name": data['name'],
            "employeeId": data['id'], # Changed key name for clarity
            "email": data['email'],
            "project": data['project'],
            "manager": data['manager'],
            "location": data['location'],
            "fromDate": data['from'], # Changed key name
            "toDate": data['to'],     # Changed key name
            "reason": data['reason'],
            "status": "Pending",      # Default status
            "submittedAt": datetime.utcnow() # Record submission time in UTC
        }

        insert_result = requests_collection.insert_one(request_document)
        logging.info(f"Successfully inserted request with _id: {insert_result.inserted_id}")

        # Prepare response data (use the actual inserted document structure)
        response_data = request_document.copy()
        response_data['_id'] = str(insert_result.inserted_id) # Return the unique MongoDB ID
        response_data['submittedAt'] = request_document['submittedAt'].isoformat() + "Z" # ISO format for JS

        return jsonify({
            "success": True,
            "message": "Request submitted successfully!",
            "request": response_data # Send back the saved data
            }), 201 # HTTP status 201 Created

    except Exception as e:
        logging.exception("Error inserting request into MongoDB:") # Log the full error
        return jsonify({"success": False, "message": "An internal server error occurred during submission."}), 500


@app.route('/view_requests')
def view_requests():
    """ Serves the admin/view page with requests from the database. """
    if requests_collection is None:
        logging.error("View requests failed: Database not available.")
        return render_template('view_submissions.html', requests=[], error="Database connection failed. Cannot load requests.")

    try:
        # Fetch all requests, sort by submission time descending
        # You could filter here, e.g., find({"status": "Pending"})
        all_requests = list(requests_collection.find().sort("submittedAt", -1))
        logging.info(f"Fetched {len(all_requests)} requests for viewing.")
        # Pass the list of request documents to the template
        return render_template('view_submissions.html', requests=all_requests)
    except Exception as e:
        logging.exception("Error fetching requests from MongoDB for viewing:")
        return render_template('view_submissions.html', requests=[], error="Could not fetch requests due to a server error.")


@app.route('/update_status/<request_id>', methods=['POST'])
def update_status(request_id):
    """ Handles updating the status (Approve/Reject) of a specific request via its _id. """
    if requests_collection is None:
        logging.error(f"Update status failed for {request_id}: Database not available.")
        return jsonify({"success": False, "message": "Database not available."}), 503

    if not request.is_json:
        return jsonify({"success": False, "message": "Request must be JSON."}), 400

    data = request.get_json()
    new_status = data.get('status')
    logging.info(f"Received status update request for ID {request_id}: new status = {new_status}")

    # Validate the new status
    if new_status not in ['Approved', 'Rejected']:
        logging.warning(f"Invalid status received for {request_id}: {new_status}")
        return jsonify({"success": False, "message": "Invalid status provided."}), 400

    try:
        # Convert the string ID from the URL to a MongoDB ObjectId
        object_id = ObjectId(request_id)
    except InvalidId:
        logging.warning(f"Invalid ObjectId format received: {request_id}")
        return jsonify({"success": False, "message": "Invalid request ID format."}), 400
    except Exception as e:
         logging.exception(f"Error converting request ID {request_id} to ObjectId:")
         return jsonify({"success": False, "message": "Error processing request ID."}), 500

    try:
        # Find the document by _id and update its status field
        update_result = requests_collection.update_one(
            {'_id': object_id},
            {'$set': {'status': new_status, 'lastUpdatedAt': datetime.utcnow()}} # Optionally track update time
        )

        if update_result.matched_count == 0:
            logging.warning(f"No request found with ID {request_id} to update.")
            return jsonify({"success": False, "message": "Request not found."}), 404
        elif update_result.modified_count == 0:
             logging.warning(f"Request {request_id} found, but status was already {new_status}.")
             # Consider this success as the desired state is achieved
             return jsonify({"success": True, "message": f"Request status is already {new_status}."})
        else:
            logging.info(f"Successfully updated status for request {request_id} to {new_status}.")
            return jsonify({"success": True, "message": "Status updated successfully!"})

    except Exception as e:
        logging.exception(f"Error updating status for request {request_id}:")
        return jsonify({"success": False, "message": "An internal server error occurred during status update."}), 500

# --- Run the App ---
if __name__ == '__main__':
    # Set debug=False for production deployments
    # Host '0.0.0.0' makes it accessible on your network, remove if only local access needed
    app.run(debug=True, host='0.0.0.0', port=5002)