import random
import string
import json
import requests
from flask import Flask, render_template, request

# Initialize Flask app
app = Flask(__name__)

# Define the path for the database (twapi.json)
DATABASE_FILE = 'twapi.json'


# Function to generate a random 32-character key
def generate_key():
    return ''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(32))


# Function to load the database from the JSON file
def load_db():
    try:
        with open(DATABASE_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


# Function to save the database to the JSON file
def save_db(db):
    with open(DATABASE_FILE, 'w') as f:
        json.dump(db, f, indent=4)


@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        domain = request.form['domain']
        # Perform a GET request to the domain
        response = requests.get(f'http://{domain}')
        
        # Check if the response text matches the expected "No peers for this domain."
        if response.text == "No peers for this domain.":
            # Generate a new 32-character key
            key = generate_key()

            # Load the current database
            db = load_db()
            
            # Check if the domain is an IP address
            if len(domain.split(".")) == 4:
                return "You can't register IP addresses."
                
            # Check if the domain already exists in the database | IF YOU ARE MAKING YOUR OWN PUBLIC SERVER CHANGE THE DISCORD USERNAME WITH YOURS.
            if domain in db:
                return f"This domain already has a key, if you lost it, please contact @milodev123 in discord."
            else:
                # Add the new domain and key to the database
                db[domain] = key
            
            # Save the updated database
            save_db(db)

            return f"Key for {domain}: {key}"

        else:
            # IF YOU ARE MAKING YOUR OWN PUBLIC SERVER CHANGE THE DOMAIN WITH YOUR CUSTOM ONE.
            return f"Domain/Subdomain specified isn't pointing to twapi-endpoint.milosantos.com"

    return '''
        <form method="POST">
            <label for="domain">Enter domain:</label><br>
            <input type="text" id="domain" name="domain"><br><br>
            <input type="submit" value="Submit">
        </form>
    '''


if __name__ == '__main__':
    app.run(host="0.0.0.0")
