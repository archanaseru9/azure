import azure.functions as func
import logging
import pyodbc
import pandas as pd
from azure.identity import ManagedIdentityCredential
from azure.keyvault.secrets import SecretClient
import os
app = func.FunctionApp()

def get_secrets(secret_name):
    logging.info("fetcing the keyvault URL")
    key_vault_url = os.environ["KEY_VAULT_URL"]
    logging.info("fetcing the keyvault URL",key_vault_url)
    credential = ManagedIdentityCredential()
    logging.info("fetcing the managed credential",credential)
    client = SecretClient(vault_url=key_vault_url, credential=credential)
    try:
        secret=client.get_secret(secret_name)
        logging.info("retrieved secret ")
        return secret.value
    except Exception as e:
        logging.error(f"Error accessing key vault:{e}")
        return None


@app.blob_trigger(arg_name="myblob", path="datacontainer/{name}.csv",
                               connection="staccountblob_STORAGE") 
def csvfiles(myblob: func.InputStream):
    logging.info(f"Python blob trigger function processed blob"
                f"Name: {myblob.name}"
                f"Blob Size: {myblob.length} bytes")
    try:
        logging.info("reading csv file")
        df= pd.read_csv(myblob)
        logging.info(f"CSV file read successfully. Number of rows: {len(df)}")
    except:
        logging.error(f"Error reading CSV file: {e}")
        return
    
    server_name = get_secrets("sqlServerName")
    password= get_secrets("sqlPassword")
    database_name = get_secrets("dataBaseName")
    username= get_secrets("sqlUserName")
        # Check if secrets were retrieved successfully
    if not all([server_name, database_name, username, password]):
        logging.error("Failed to retrieve one or more secrets from Key Vault.")
        return

    # Connect to Azure SQL Database
    try:
        logging.info("Connecting to Azure SQL Database...")
        conn = pyodbc.connect(
            f'DRIVER={{ODBC Driver 18 for SQL Server}};'
            f'SERVER=tcp:{server_name};'
            f'DATABASE={database_name};'
            f'UID={username};'
            f'PWD={password};'
            'Encrypt=yes;'
            'TrustServerCertificate=no;'
            'Connection Timeout=30;'
        )
        cursor = conn.cursor()
        logging.info("Connected to Azure SQL Database successfully.")
    except Exception as e:
        logging.error(f"Error connecting to SQL Database: {e}")
        return
    
    # Upsert data into Products table using MERGE
    try:
        logging.info("Upserting data into Products table...")
        for index, row in df.iterrows():
            merge_query = """
            MERGE INTO Products AS Target
            USING (VALUES (?, ?, ?, ?, ?)) AS Source (ProductID, ProductName, Category, QuantityInStock, Price)
            ON Target.ProductID = Source.ProductID
            WHEN MATCHED THEN
                UPDATE SET
                    ProductName = Source.ProductName,
                    Category = Source.Category,
                    QuantityInStock = Source.QuantityInStock,
                    Price = Source.Price
            WHEN NOT MATCHED THEN
                INSERT (ProductID, ProductName, Category, QuantityInStock, Price)
                VALUES (Source.ProductID, Source.ProductName, Source.Category, Source.QuantityInStock, Source.Price);
            """

            cursor.execute(merge_query,
                           row['ProductID'],
                           row['ProductName'],
                           row['Category'],
                           row['QuantityInStock'],
                           row['Price'])
            logging.info(f"Upserted ProductID {row['ProductID']}")
        conn.commit()
        logging.info("Data upserted successfully.")

    except Exception as e:
        logging.error(f"Error upserting data: {e}")
    finally:
        cursor.close()
        conn.close()
        logging.info("Database connection closed.")


    # Notify Logic App via HTTP POST
    try:
        logic_app_url = "https://prod-26.australiaeast.logic.azure.com:443/workflows/dac595749fdb4661994a85fd29767c8c/triggers/When_a_HTTP_request_is_received/paths/invoke?api-version=2016-10-01&sp=%2Ftriggers%2FWhen_a_HTTP_request_is_received%2Frun&sv=1.0&sig=NampEWgeC3ijMqNyikwdLeTMxYVn8P1xycyabvUAtwM"  # Replace with your actual Logic App URL
        payload = {
            "message": "SQL database has been updated with the new inventory."
        }

        headers = {
            'Content-Type': 'application/json'
        }

        logging.info("Sending POST request to Logic App...")
        response = requests.post(logic_app_url, data=json.dumps(payload), headers=headers)

        if (response.status_code == 200) or (response.status_code == 202):
            logging.info(f"Notification sent successfully. Response: {response.text}")
        else:
            logging.error(f"Failed to send notification. Status Code: {response.status_code}, Response: {response.text}")
    except Exception as e:
        logging.error(f"Error sending POST request to Logic App: {e}")

    return