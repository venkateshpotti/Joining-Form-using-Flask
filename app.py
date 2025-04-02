from flask import Flask, render_template, request, redirect, url_for
from pymongo import MongoClient
import os
import pymongo
import logging
from bson import ObjectId  # Import ObjectId
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')

# Configure logging
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# MongoDB Configuration
MONGO_URI = os.environ.get('MONGO_URI')
DATABASE_NAME = "job_application_db"

try:
    client = MongoClient(MONGO_URI)
    db = client[DATABASE_NAME]
    applications_collection = db["applications"]
    logging.info("Connected to MongoDB successfully!")
    try:
        client.admin.command('ping')
        logging.info("Successfully pinged MongoDB server!")
    except Exception as e:
        logging.error(f"Failed to ping MongoDB server: {e}")
        exit()
except Exception as e:
    logging.error(f"Error connecting to MongoDB: {e}")
    exit()

@app.route('/', methods=['GET', 'POST'])
def job_application_form():
    logging.debug("Received request to job_application_form")

    if request.method == 'POST':
        logging.debug("Received POST request")
        form_data = request.form.to_dict()
        logging.debug(f"Form data: {form_data}")

        # Server-side data validation - Add more as needed!!
        required_fields = [
            'department', 'jobRole', 'branchLocation', 'expectedSalary', 'interviewDate',
            'joiningDate', 'employmentType', 'FullName', 'email', 'dob', 'mobileNumber',
            'fatherName', 'permanentAddress', 'sscYear', 'sscPercentage', 'intermediateYear',
            'intermediatePercentage', 'higherGraduation', 'registerNumber', 'graduationYear',
            'graduationPercentage', 'experienceStatus'
        ]
        for field in required_fields:
            if not form_data.get(field):
                return render_template('form.html', error=f"{field.capitalize()} is required.", **form_data)

        # More robust email validation (optional but recommended) - Use a regex library
        # Example (you'll need to install "validators" with "pip install validators"):
        # if not validators.email(form_data['email']):
        #    return render_template('form.html', error="Invalid email format.", **form_data)

        try:
            # Insert the form data into MongoDB
            result = applications_collection.insert_one(form_data)
            logging.info("Form data saved to MongoDB")
            inserted_id = result.inserted_id
            logging.debug(f"Inserted document ID: {inserted_id}")
            return redirect(url_for('success', id=str(inserted_id)))  #Pass inserted_id to success
        except pymongo.errors.ConnectionFailure as e:
            logging.exception("MongoDB connection failure")
            return render_template('form.html', error="Failed to connect to MongoDB. Please check your connection.", **form_data)
        except pymongo.errors.OperationFailure as e:
            logging.exception("MongoDB operation failure")
            return render_template('form.html', error=f"MongoDB operation failed: {e}",  **form_data)
        except Exception as e:
            logging.exception("General error saving to MongoDB")
            return render_template('form.html', error="An unexpected error occurred while saving data.", **form_data)

    return render_template('form.html')

@app.route('/success')
def success():
    inserted_id = request.args.get('id')
    logging.debug(f"Displaying success page for ID: {inserted_id}")

    if not inserted_id:
        logging.warning("No ID provided in success route")
        return "Error: No ID provided", 400

    try:
        # Fetch the document from MongoDB based on the ID
        application = applications_collection.find_one({"_id": ObjectId(inserted_id)})

        if application:
            logging.debug(f"Application data found: {application}")
            return render_template('success.html', application=application)
        else:
            logging.warning(f"Application not found with ID: {inserted_id}")
            return "Error: Application not found", 404
    except Exception as e:
        logging.exception("Error fetching data from MongoDB")
        return "Error fetching data from MongoDB", 500

if __name__ == '__main__':
    app.run(debug=True)