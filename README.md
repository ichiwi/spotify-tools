# Spotify Organizer

A Flask web app that uses the Spotify Web API to inspect liked songs and
playlists. It compares normalized artist and track names, so the same song
from different releases can be identified together. Known release markers are
ignored: `Song`, `Song - Remastered 2011`, and `Song (Deluxe Edition)` are
grouped, while `Song 2` and tracks by different artists remain separate.

## Features

- Find duplicate liked songs.
- Find duplicate tracks across one or more selected playlists you own.
- Find songs repeated across multiple selected playlists.
- Find selected playlist tracks that are not in your liked songs.
- Find liked songs that are not in any selected playlist.
- Select playlists from a cover-based picker. Playlists owned by other users
  are excluded.
- Run scans in the background with progress updates.

## Requirements

- Python 3
- A Spotify Developer application
- A Spotify account

## Spotify app setup

1. Create an app in the
   [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
2. Add `http://127.0.0.1:5000/callback` as a redirect URI.
3. Copy `.env.example` to `.env` and set:

   ```text
   SPOTIFY_CLIENT_ID=your_client_id
   SPOTIFY_CLIENT_SECRET=your_client_secret
   FLASK_SECRET_KEY=your_random_secret
   FLASK_DEBUG=1
   ```

`FLASK_SECRET_KEY` and `FLASK_DEBUG` are optional. If `FLASK_DEBUG` is omitted,
the app runs with debug mode disabled.

## Setup

Create a virtual environment, install dependencies, and start the server:

```text
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000` and connect your Spotify account.

## Styles

The source stylesheet is
[static/scss/main.scss](static/scss/main.scss). Compile it to
[static/css/main.css](static/css/main.css) with Sass after making style
changes:

```text
sass static/scss/main.scss static/css/main.css
```

Generated CSS files are excluded from Git, so compile the stylesheet locally
before running the website.

## Spotify permissions and API usage

The app requests read-only Spotify scopes:

- `user-library-read`
- `playlist-read-private`
- `playlist-read-collaborative`

It loads saved tracks with `GET /me/tracks`, owned playlists with `GET
/me/playlists`, and selected playlist items with `GET
/playlists/{playlist_id}/items`. The app uses `GET /me` to identify the signed-in
account and excludes followed playlists and playlists owned by other users.

Keep `.env` out of version control. It contains credentials that should not be
shared or committed.
