# Spotify Duplicate Finder

A small Flask website that signs in with Spotify and lists duplicate songs in
liked songs or a playlist, or finds liked songs that are missing from every
playlist. A duplicate requires the same artist and the same underlying title.
Known release markers are ignored, so `Song`, `Song -
Remastered 2011`, and `Song (Deluxe Edition)` are grouped, while `Song 2` and
tracks by different artists are not.

## Setup

1. Create an app in the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
   and add `http://127.0.0.1:5000/callback` as a Redirect URI.
2. Copy `.env.example` to `.env` and add the app's client ID and secret.
3. Install dependencies and run:

   ```text
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   python filter.py
   ```

4. Open `http://127.0.0.1:5000`.

The app requests only read scopes (`user-library-read`,
`playlist-read-private`, and `playlist-read-collaborative`). It uses Spotify's
documented `GET /me/tracks` endpoint for saved tracks and
`GET /playlists/{playlist_id}/items` for playlist items. The all-playlists
check uses `GET /me` to identify the signed-in user, then compares tracks only
from playlists whose owner is that user. Followed and other users' playlists
are excluded.
