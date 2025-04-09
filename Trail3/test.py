import requests
import json
from bs4 import BeautifulSoup # Import BeautifulSoup

# --- Configuration ---
FLASK_APP_URL = "http://127.0.0.1:5000" # Make sure this matches your app's host/port
VIEW_REQUESTS_URL = f"{FLASK_APP_URL}/view_requests"
UPDATE_STATUS_URL_TEMPLATE = f"{FLASK_APP_URL}/update_status/{{request_id}}" # Template for the update URL

# Set the desired new status for the FIRST request found
NEW_STATUS = "Approved" # Or "Rejected"
# --- End Configuration ---

def get_first_request_id():
    """Fetches the view page and extracts the _id of the first request."""
    print(f"Fetching requests page: {VIEW_REQUESTS_URL}")
    try:
        response = requests.get(VIEW_REQUESTS_URL, timeout=10) # Add timeout
        response.raise_for_status() # Raise error for bad status codes (4xx, 5xx)

        # Parse the HTML content
        soup = BeautifulSoup(response.text, 'lxml') # Use lxml parser

        # Find the container for submission items
        submission_list = soup.find('div', id='submissionList')
        if not submission_list:
             print("Error: Could not find submission list container ('#submissionList') on the page.")
             return None

        # Find the first submission item div within the list
        # It should have the class 'submission-item' and a 'data-request-id' attribute
        first_item = submission_list.find('div', class_='submission-item', attrs={'data-request-id': True})

        if first_item and 'data-request-id' in first_item.attrs:
            request_id = first_item['data-request-id']
            print(f"Found request ID: {request_id}")
            return request_id
        else:
            print("No submission items with 'data-request-id' found on the page.")
            # Check if the 'no-data' message is present
            no_data_div = submission_list.find('div', class_='no-data')
            if no_data_div:
                print("Reason: 'No Requests Found' message is displayed.")
            return None

    except requests.exceptions.ConnectionError as e:
        print(f"\nError: Could not connect to the server at {FLASK_APP_URL}.")
        print("Please ensure your Flask application is running.")
        return None
    except requests.exceptions.Timeout:
         print(f"\nError: Request to {VIEW_REQUESTS_URL} timed out.")
         return None
    except requests.exceptions.HTTPError as e:
         print(f"\nError: Failed to fetch view page. Status Code: {response.status_code} ({response.reason})")
         return None
    except Exception as e:
        print(f"\nAn unexpected error occurred while fetching/parsing the view page: {e}")
        return None


def send_status_update(request_id, status):
    """Sends the POST request to update the status for the given request ID."""
    update_url = UPDATE_STATUS_URL_TEMPLATE.format(request_id=request_id)
    payload = {"status": status}
    headers = {"Content-Type": "application/json"}

    print(f"\nAttempting to send POST request to: {update_url}")
    print(f"With JSON payload: {json.dumps(payload)}")

    try:
        response = requests.post(update_url, headers=headers, json=payload, timeout=10)
        response.raise_for_status() # Check for 4xx/5xx errors

        print("\nRequest Successful!")
        print(f"Status Code: {response.status_code}")
        try:
            print("Response JSON:")
            print(json.dumps(response.json(), indent=2))
        except json.JSONDecodeError:
            print("Response Body (non-JSON):")
            print(response.text)
        return True # Indicate success

    except requests.exceptions.ConnectionError:
        print(f"\nError: Could not connect to the server at {FLASK_APP_URL} to update status.")
        return False
    except requests.exceptions.Timeout:
         print(f"\nError: Update request to {update_url} timed out.")
         return False
    except requests.exceptions.HTTPError as e:
        print(f"\nError: HTTP Error Occurred during status update.")
        print(f"Status Code: {response.status_code} ({response.reason})")
        try:
            print("Server Response:")
            print(json.dumps(response.json(), indent=2))
        except json.JSONDecodeError:
            print("Server Response (non-JSON):")
            print(response.text)
        return False
    except requests.exceptions.RequestException as e:
        print(f"\nError: An error occurred during the update request: {e}")
        return False
    except Exception as e:
         print(f"\nAn unexpected error occurred during status update: {e}")
         return False

# --- Main Execution ---
if __name__ == "__main__":
    extracted_request_id = get_first_request_id()

    if extracted_request_id:
        print(f"\nProceeding to update status for request ID: {extracted_request_id} to '{NEW_STATUS}'")
        send_status_update(extracted_request_id, NEW_STATUS)
    else:
        print("\nCould not extract a request ID. Status update aborted.")