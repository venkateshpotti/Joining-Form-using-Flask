import os
import pandas as pd
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure
from dotenv import load_dotenv
from rich import print
# 1. Load Configuration
load_dotenv()
MONGO_URI = os.getenv("MONGO_URI")
DATABASE_NAME = os.getenv("DATABASE_NAME", "leave_management")
COLLECTION_NAME = "applications" # Your collection name

if not MONGO_URI:
    print("❌ ERROR: MONGO_URI not set in .env file.")
    exit(1)

client = None # Initialize client outside try block

try:
    # 2. Connect to MongoDB
    print(f"⏳ Connecting to MongoDB (DB: {DATABASE_NAME}, Collection: {COLLECTION_NAME})...")
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db = client[DATABASE_NAME]
    collection = db[COLLECTION_NAME]
    # Simple check if collection seems accessible (optional)
    collection.find_one(limit=1)
    print("✅ Connection successful.")

    # 3. Read Data into a List
    print("⏳ Reading data...")
    cursor = collection.find({}) # Find all documents
    data_list = list(cursor)    # Convert cursor to list of dictionaries

    if not data_list:
        print("ℹ️ No documents found in the collection.")
    else:
        # 4. Create Pandas DataFrame
        print(f"✅ Found {len(data_list)} documents. Creating DataFrame...")
        df = pd.DataFrame(data_list)

        # 5. Display DataFrame Info and Head
        print("\n--- DataFrame Info ---")
        df.info()

        print("\n--- DataFrame Head (First 5 Rows) ---")
        # Configure pandas display options for better console output
        pd.set_option('display.max_columns', None) # Show all columns
        pd.set_option('display.width', 1000)       # Adjust width
        print(df.head())

        # print((df[df["leaveType"]== "Earned") & (df['fromDate']=="2025-04-24")])
        print(df[(df["leaveType"] == "Earned") & (df["fromDate"] > "2025-01-01")])



        # You can now work with the DataFrame 'df'

except ConnectionFailure as ce:
    print(f"❌ MongoDB Connection Failure: {ce}")
except Exception as e:
    print(f"❌ An error occurred: {e}")

finally:
    # 6. Close Connection (Important!)
    if client:
        client.close()
        print("\n🔌 MongoDB connection closed.")