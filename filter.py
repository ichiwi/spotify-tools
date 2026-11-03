import os
import re
import secrets
import time
from functools import wraps
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, session, url_for

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))

SPOTIFY_ACCOUNTS_URL = "https://accounts.spotify.com"
SPOTIFY_API_URL = "https://api.spotify.com/v1"
OAUTH_SCOPES = "user-library-read playlist-read-private playlist-read-collaborative"


def spotify_configured():
    return bool(os.environ.get("SPOTIFY_CLIENT_ID") and os.environ.get("SPOTIFY_CLIENT_SECRET"))


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "access_token" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def spotify_request(path_or_url, method="GET", **kwargs):
    url = path_or_url if path_or_url.startswith("http") else f"{SPOTIFY_API_URL}{path_or_url}"
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {session['access_token']}"
    last_error = None
    for attempt in range(2):
        try:
            response = requests.request(
                method,
                url,
                headers=headers,
                timeout=(10, 30),
                **kwargs,
            )
            break
        except requests.RequestException as error:
            last_error = error
            if attempt == 0:
                time.sleep(1)
    else:
        raise RuntimeError(
            "Spotify could not be reached. Check your internet connection and try again."
        ) from last_error

    if response.status_code == 401:
        session.clear()
        raise RuntimeError("Your Spotify session expired. Please sign in again.")
    if not response.ok:
        try:
            message = response.json().get("error", {}).get("message", response.text)
        except ValueError:
            message = response.text
        raise RuntimeError(f"Spotify returned an error: {message}")
    return response.json()


def fetch_all_pages(path):
    items = []
    next_url = path
    while next_url:
        page = spotify_request(next_url)
        items.extend(page.get("items", []))
        next_url = page.get("next")
    return items


def track_from_item(item):
    track = item.get("track") or item
    if not track or track.get("type") != "track":
        return None
    artists = track.get("artists") or []
    if not track.get("name") or not artists:
        return None
    return {
        "name": track["name"],
        "artist": ", ".join(artist["name"] for artist in artists),
        "album": (track.get("album") or {}).get("name", ""),
        "url": (track.get("external_urls") or {}).get("spotify", ""),
    }


def normalise(value):
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


RELEASE_SUFFIX = (
    r"(?:"
    r"(?:\d{4}\s+)?"
    r"(?:remaster(?:ed)?|deluxe|anniversary|expanded|special|"
    r"collector'?s?|original|mono|stereo|acoustic|live|radio|single|"
    r"album|bonus|edit|mix|version|re-recorded|reimagined|edition)"
    r"(?:\s+(?:edition|version|mix|edit|recording|remaster(?:ed)?))?"
    r"(?:\s+\d{4})?"
    r")"
)

def release_independent_title(value):
    title = normalise(value)
    while True:
        without_suffix = re.sub(rf"\s+{RELEASE_SUFFIX}$", "", title).strip()
        if without_suffix == title:
            return title
        title = without_suffix


def find_duplicates(tracks):
    groups = []
    for track in tracks:
        title = release_independent_title(track["name"])
        artist = normalise(track["artist"])
        matching_group = next(
            (
                group
                for group in groups
                if group["artist_key"] == artist and group["title_key"] == title
            ),
            None,
        )
        if matching_group:
            matching_group["tracks"].append(track)
        else:
            groups.append({"artist_key": artist, "title_key": title, "tracks": [track]})
    return [group["tracks"] for group in groups if len(group["tracks"]) > 1]


def track_key(track):
    return (release_independent_title(track["name"]), normalise(track["artist"]))


def find_unfiled_tracks(liked_tracks, playlist_tracks):
    playlist_keys = {track_key(track) for track in playlist_tracks}
    return [track for track in liked_tracks if track_key(track) not in playlist_keys]


@app.route("/")
def index():
    return render_template("index.html", logged_in="access_token" in session)


@app.route("/login")
def login():
    if not spotify_configured():
        flash("Add SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET to .env first.", "error")
        return redirect(url_for("index"))
    session["oauth_state"] = secrets.token_urlsafe(24)
    params = {
        "client_id": os.environ["SPOTIFY_CLIENT_ID"],
        "response_type": "code",
        "redirect_uri": url_for("callback", _external=True),
        "scope": OAUTH_SCOPES,
        "state": session["oauth_state"],
    }
    return redirect(f"{SPOTIFY_ACCOUNTS_URL}/authorize?{urlencode(params)}")


@app.route("/callback")
def callback():
    if request.args.get("state") != session.pop("oauth_state", None):
        flash("Spotify sign-in could not be verified.", "error")
        return redirect(url_for("index"))
    if "error" in request.args:
        flash(f"Spotify sign-in failed: {request.args['error']}", "error")
        return redirect(url_for("index"))
    try:
        response = requests.post(
            f"{SPOTIFY_ACCOUNTS_URL}/api/token",
            data={
                "grant_type": "authorization_code",
                "code": request.args["code"],
                "redirect_uri": url_for("callback", _external=True),
            },  
            auth=(os.environ["SPOTIFY_CLIENT_ID"], os.environ["SPOTIFY_CLIENT_SECRET"]),
            timeout=30,
        )
        response.raise_for_status()
        token = response.json()
    except (requests.RequestException, KeyError, ValueError) as error:
        flash(f"Could not complete Spotify sign-in: {error}", "error")
        return redirect(url_for("index"))
    access_token = token.get("access_token")
    if not access_token:
        flash("Spotify did not return an access token. Please try again.", "error")
        return redirect(url_for("index"))
    session["access_token"] = access_token
    session["refresh_token"] = token.get("refresh_token")
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/scan", methods=["POST"])
@login_required
def scan():
    source = request.form.get("source")
    playlist_id = request.form.get("playlist_id", "").strip()
    if source == "all_playlists":
        try:
            liked_tracks = [
                track
                for item in fetch_all_pages("/me/tracks?limit=50")
                if (track := track_from_item(item))
            ]
            playlists = fetch_all_pages("/me/playlists?limit=50")
            playlist_tracks = []
            for playlist in playlists:
                playlist_tracks.extend(
                    track
                    for item in fetch_all_pages(
                        f"/playlists/{playlist['id']}/items?limit=50"
                    )
                    if (track := track_from_item(item))
                )
            missing = find_unfiled_tracks(liked_tracks, playlist_tracks)
        except (KeyError, RuntimeError) as error:
            flash(str(error), "error")
            return redirect(url_for("index"))
        return render_template(
            "results.html",
            missing=missing,
            playlist_count=len(playlists),
            source_name="Liked songs not in your playlists",
            track_count=len(liked_tracks),
            logged_in=True,
        )
    if source == "liked":
        endpoint = "/me/tracks?limit=50"
        source_name = "Liked songs"
    elif source == "playlist" and re.fullmatch(r"[A-Za-z0-9]+", playlist_id):
        endpoint = f"/playlists/{playlist_id}/items?limit=50&fields=items(track(name,artists,album,external_urls,type)),next"
        source_name = f"Playlist {playlist_id}"
    else:
        flash("Choose liked songs or enter a playlist ID.", "error")
        return redirect(url_for("index"))
    try:
        tracks = [track for item in fetch_all_pages(endpoint) if (track := track_from_item(item))]
        duplicates = find_duplicates(tracks)
    except RuntimeError as error:
        flash(str(error), "error")
        return redirect(url_for("index"))
    return render_template(
        "results.html",
        duplicates=duplicates,
        source_name=source_name,
        track_count=len(tracks),
        logged_in=True,
    )


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
