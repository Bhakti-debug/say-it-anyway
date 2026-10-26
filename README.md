# Quiet Space

A small feelings-sharing app. People can browse the feed, create an account with a username, post thoughts with an optional photo into a section, follow other accounts, leave emoji reactions, and comment. The signup form does not ask for a real name or email address. FastAPI provides the API. Local development uses SQLite and local image files; the hosted version uses Supabase PostgreSQL and Supabase Storage.

## Run it locally (Windows)

Install Python 3.13, Node.js 22 or newer, and npm if they are not already installed. Then double-click `start.bat`. The first run downloads the app packages and can take a little while. When the terminal says the server is running, open http://127.0.0.1:8000. Keep the terminal window open while using the app; press Ctrl+C there to stop it.

## Files, in the order to learn them

- `app.py` — FastAPI routes and the SQLite database.
- `frontend/src/App.jsx` — React components, page state, and the post form.
- `frontend/src/style.css` — the visual design and responsive layout.
- `frontend/vite.config.js` — the frontend development server and API connection.
- `Dockerfile` — the frontend build and FastAPI app for a container host.
- `vercel.json` — the React build and static files included in the Vercel deployment.

Photos are optional JPG, PNG, or WebP files up to 8 MB. The app converts uploads to resized WebP files in the local `uploads/` folder. Passwords are stored as Argon2 hashes, not readable passwords. New posts show the chosen username publicly in the feed. Users can delete only posts and comments owned by their account; moderators can also remove other people's content and review reports. Moderator access is assigned manually from the machine that owns the database, never by choosing a username. After the account exists, run `.venv\Scripts\python.exe manage_admin.py promote username` from this folder. To remove moderator access, run `.venv\Scripts\python.exe manage_admin.py demote username`. The learning MVP still needs password recovery and rate limits before a public launch.

Posting uses a normal HTTP request: React sends the post to FastAPI, FastAPI saves it, and React adds the returned post to the feed. A WebSocket is unnecessary for this flow. We can add one later if we want the feed to update instantly for other people without them refreshing.

The `start.bat` file builds the React page and serves it through FastAPI. For frontend development with hot reload, open two terminals in this folder: run `\.venv\Scripts\python.exe -m uvicorn app:app` in one, then `npm run dev` from `frontend` in the other and visit http://localhost:5173.

For Vercel hosting, the app uses Supabase PostgreSQL for posts and Supabase Storage for photos. Set `DATABASE_URL` to Supabase's Transaction pooler connection string, and set `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, and `SESSION_SECRET` in Vercel's Environment settings. Keep the service role key private. Create a public Storage bucket named `post-images` (or set `SUPABASE_STORAGE_BUCKET` to another bucket name) before deploying. Set `COOKIE_SECURE=true`. The build configuration is in `vercel.json`; Vercel runs FastAPI as a serverless function and builds the React page before deployment.

The deployed Supabase database starts empty and is separate from the local SQLite file. After creating `@softchaos` on the hosted app, grant moderator access in Supabase SQL Editor with `UPDATE users SET is_moderator = TRUE WHERE LOWER(username) = LOWER('softchaos');`.

The existing `quiet_space.db` is kept, so any posts from the first version remain in the feed. This is a learning MVP, not ready for public deployment. Add moderation/reporting, abuse controls, privacy details, and deployment settings before inviting real users.
