
# ESPORTS Tournament Management System

A DBMS project for managing esports tournaments, teams, players, matches, and tournament analytics.

## Features

- Dashboard with tournament and match insights
- Team performance charts and match outcome visualization
- Custom report builder with automatically generated SQL
- Database explorer with search and CRUD operations
- CockroachDB integration using Python and Flask

## Technology Stack

- Python
- Flask
- CockroachDB
- pg8000
- HTML, CSS, JavaScript
- Bootstrap and Chart.js

## Run Locally

1. Install Python.
2. Clone this repository.
3. Create and activate a virtual environment.
4. Install dependencies:

   ```bash
   python -m pip install -r requirements.txt
   ```

5. Set the `DATABASE_URL` environment variable to your CockroachDB connection URL.
6. Start the application:

   ```bash
   python app.py
   ```

7. Open http://127.0.0.1:5000 in your browser.

## Database Configuration

Do not commit database passwords or credentials. Each developer should configure their own `DATABASE_URL` environment variable.

## Deployment

Deployment and final testing will be completed after project review.
