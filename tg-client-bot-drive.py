import os
import io
import argparse
import logging
from datetime import datetime
from dotenv import load_dotenv
from telethon.sync import TelegramClient
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# Ottieni la data e l'ora corrente per il nome del file di log (ora e minuti inclusi)
current_datetime = datetime.now().strftime("%Y-%m-%d_%H-%M")
log_filename = f"log_{current_datetime}.txt"

# Configurazione del logging per scrivere su file
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    filename=log_filename,
                    filemode='w')

# Carica le variabili di ambiente dal file .env
load_dotenv()

# Variabili di ambiente
api_id = os.getenv('API_ID')
api_hash = os.getenv('API_HASH')
google_credentials_path = os.getenv('GOOGLE_CREDENTIALS_PATH')
drive_folder_id = os.getenv('DRIVE_FOLDER_ID')

class TelegramSession:
    def __init__(self, session_name):
        self.session_name = session_name
        self.client = TelegramClient(session_name, api_id, api_hash)

async def get_active_chats(client):
    logging.info("Connessione al client Telegram...")
    await client.connect()
    if not await client.is_user_authorized():
        phone_number = input("Inserisci numero di telefono: ")
        await client.send_code_request(phone_number)
        await client.sign_in(phone_number, input('Inserisci il codice: '))
    logging.info("Connessione al client Telegram stabilita.")
    return await client.get_dialogs(), await client.get_me()

def authenticate_google_drive():
    logging.info("Autenticazione su Google Drive in corso...")
    creds = Credentials.from_service_account_file(google_credentials_path)
    service = build('drive', 'v3', credentials=creds)
    logging.info("Autenticazione su Google Drive completata.")
    return service

def create_drive_folder(service, folder_name, parent_folder_id):
    logging.info(f"Creazione di una nuova cartella '{folder_name}' su Google Drive...")
    file_metadata = {
        'name': folder_name,
        'mimeType': 'application/vnd.google-apps.folder',
        'parents': [parent_folder_id]
    }
    folder = service.files().create(body=file_metadata, fields='id').execute()
    folder_id = folder.get('id')
    logging.info(f"Nuova cartella '{folder_name}' creata con ID: {folder_id}")
    return folder_id


def get_or_create_drive_folder(service, folder_name, parent_folder_id):
    logging.info(f"Controllo dell'esistenza della cartella '{folder_name}' su Google Drive...")

    # Cerca la cartella
    query = f"name = '{folder_name}' and '{parent_folder_id}' in parents and mimeType = 'application/vnd.google-apps.folder'"
    response = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
    folders = response.get('files', [])

    # Se la cartella esiste già, usa quella
    if folders:
        folder_id = folders[0].get('id')
        logging.info(f"La cartella esiste già con ID: {folder_id}. Pulizia della cartella...")
        delete_files_in_folder(service, folder_id)
    else:
        # Altrimenti, crea una nuova cartella
        folder_id = create_drive_folder(service, folder_name, parent_folder_id)

    return folder_id

def delete_files_in_folder(service, folder_id):
    response = service.files().list(q=f"'{folder_id}' in parents", spaces='drive', fields='files(id)').execute()
    files = response.get('files', [])
    for file in files:
        service.files().delete(fileId=file.get('id')).execute()

def save_chat_to_drive(service, name, username, phone_number, messages, myuser, folder_id):
    if len(messages) <= 1 and str(messages[0].text) == "None": 
        logging.info(f"Nessun messaggio da salvare per la chat '{name}'.")
        return

    logging.info(f"Elaborazione e salvataggio dei messaggi per la chat '{name}'...")
    content = ""
    for message in messages:
        sender = message.sender_id or "Unknown"
        if sender != "Unknown":
            sender = message.sender.username or message.sender.first_name or message.sender.last_name or "Unknown"

        content += f"{sender}: {message.text}\n"

    file_metadata = {
        'name': f"{name} - {username} - {phone_number}.txt",
        'mimeType': 'text/plain',
        'parents': [folder_id]  # Usa l'ID della cartella creata
    }

    # Converti la stringa di contenuto in bytes
    byte_content = content.encode('utf-8')
    media = MediaIoBaseUpload(io.BytesIO(byte_content), mimetype='text/plain', resumable=True)
    service.files().create(body=file_metadata, media_body=media, fields='id').execute()
    logging.info(f"File per la chat '{name}' salvato su Google Drive.")

async def save_to_drive(service, chats, user, client):
    myuser = user.username
    logging.info(f"Elaborazione dell'utente: {myuser}")

    # Utilizza get_or_create_drive_folder anziché create_drive_folder
    folder_id = get_or_create_drive_folder(service, myuser, drive_folder_id)

    for chat in chats:
        if chat.is_group or len(chat.title) == 0 or chat.is_channel:
            continue

        name = chat.title
        user = await client.get_entity(chat.id)
        phone_number = user.phone or "NA"

        if phone_number != "NA":
            phone_number = "+" + phone_number

        messages = await client.get_messages(chat.id, limit=None)
        
        # Passa l'ID della cartella a save_chat_to_drive per salvare il file nella cartella corretta
        save_chat_to_drive(service, name, user.username, phone_number, messages, myuser, folder_id)
        
def upload_log_to_drive(service, log_filename, parent_folder_id):
    logging.info(f"Caricamento del file di log '{log_filename}' su Google Drive...")
    file_metadata = {
        'name': log_filename,
        'parents': [parent_folder_id]
    }
    media = MediaIoBaseUpload(filename=log_filename, mimetype='text/plain')
    service.files().create(body=file_metadata, media_body=media, fields='id').execute()

if __name__ == "__main__":
    logging.info("Inizio esecuzione script...")

    parser = argparse.ArgumentParser(description='Process some integers.')
    parser.add_argument("--askPhones", help="The program will ask for new sessions or not", action='store_true')
    parser.set_defaults(askPhones=False)

    args = parser.parse_args()

    sessions = []
    
    if args.askPhones:
        while True:
            session_name = input("Inserisci il nome della sessione: ")
            session = TelegramSession(session_name)
            sessions.append(session)

            active_chats, user = session.client.loop.run_until_complete(get_active_chats(session.client))
            logging.info(f"Account: {user.username or user.first_name or user.last_name or 'Unknown'}\nStatus: Aggiunto")

            if input("Vuoi continuare? (y/n): ") == "n":
                break
    else:
        for file in os.listdir("./"):
            if file.endswith('.session'):
                session_name = file.split(".")[0]
                session = TelegramSession(session_name)
                sessions.append(session)

    logging.info("Inizio salvataggio chat...")

    drive_service = authenticate_google_drive()

    for session in sessions:
        active_chats, user = session.client.loop.run_until_complete(get_active_chats(session.client))
        logging.info(f"Sessione: {session.session_name} - Account: {user.username or user.first_name or user.last_name or 'Unknown'}")
        session.client.loop.run_until_complete(save_to_drive(drive_service, active_chats, user, session.client))
        logging.info(f"Chat per la sessione '{session.session_name}' salvate su Google Drive.")

    logging.info("Script completato.")
    # Carica il file di log su Google Drive
    upload_log_to_drive(drive_service, log_filename, drive_folder_id)
