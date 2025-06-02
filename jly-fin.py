#!/usr/bin/env python3
# jly-fin, a cli music client for jellyfin
# Source code is kind of a mess(? Im going to fix a lot of this later
# - Nixietab

import os
import sys
import subprocess
import requests
import getpass
import re
import signal
import json
import atexit
from urllib.parse import urljoin

# ---- Terminal Health ----

def restore_terminal():
    # Restore sane terminal mode and show cursor
    subprocess.run(['stty', 'sane'], stderr=subprocess.DEVNULL)
    print("\033[?25h", end='')  # Show cursor

atexit.register(restore_terminal)

def signal_handler(sig, frame):
    restore_terminal()
    print("\nInterrupted. Exiting cleanly.")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# ---- Color helpers ----
class Color:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    FG_MAGENTA = '\033[35m'
    FG_CYAN = '\033[36m'
    FG_YELLOW = '\033[33m'
    FG_GREEN = '\033[32m'
    FG_RED = '\033[31m'
    FG_BLUE = '\033[34m'
    FG_WHITE = '\033[37m'

def color(text, style):
    return f"{style}{text}{Color.ENDC}"

def strip_ansi(s):
    return re.sub(r'\033\[[0-9;]*m', '', s)

def input_nonempty(prompt):
    return input(color(prompt, Color.FG_CYAN + Color.BOLD))

def normalize_url(url):
    if not re.match(r'^https?://', url):
        url = 'http://' + url
    return url.rstrip('/')

# ----- Multi-server credential storage -----
def env_path():
    return os.path.expanduser('~/.jellyfin_fzf_servers.json')

def save_servers(servers):
    try:
        with open(env_path(), 'w') as f:
            json.dump(servers, f, indent=4)
        os.chmod(env_path(), 0o600)
        print(color(f"Servers saved to {env_path()} (only readable by you)", Color.OKGREEN))
    except Exception as e:
        print(color(f"Failed to save servers: {e}", Color.FAIL))

def load_servers():
    if not os.path.exists(env_path()):
        return {}
    try:
        with open(env_path(), 'r') as f:
            return json.load(f)
    except Exception:
        return {}

def add_server_interactive(servers):
    base_url = normalize_url(input_nonempty("Jellyfin Base URL (e.g. https://yourjellyfin:8096): "))
    username = input_nonempty("Username: ")
    password = getpass.getpass(color("Password: ", Color.FG_CYAN + Color.BOLD))
    save = input(color("Save this server for future use? [y/N] ", Color.FG_YELLOW)).strip().lower()
    if save == "y":
        while True:
            name = input_nonempty("Friendly name for this server: ")
            if name in servers:
                print(color("That name already exists. Please choose another.", Color.WARNING))
            else:
                break
        servers[name] = {
            "url": base_url,
            "username": username,
            "password": password
        }
        save_servers(servers)
        print(color(f"Server '{name}' added.", Color.OKGREEN))
        return name
    else:
        # Return a temporary server config (not saved)
        temp_name = "__TEMP__"
        servers[temp_name] = {
            "url": base_url,
            "username": username,
            "password": password
        }
        return temp_name

def choose_server_fzf(servers):
    options = [
        color(name, Color.FG_CYAN + Color.BOLD) + color(" (" + s["url"] + ")", Color.FG_WHITE)
        for name, s in servers.items() if name != "__TEMP__"
    ]
    options.append(color("Add another server", Color.FG_GREEN + Color.BOLD))
    sel = fzf_select(options, prompt=color("Select server > ", Color.FG_MAGENTA + Color.BOLD))
    if not sel:
        print(color("No server selected.", Color.WARNING))
        return None
    sel_clean = strip_ansi(sel[0])
    if sel_clean.strip().startswith("Add another server"):
        return "add"
    # Find which server
    for name in servers:
        if name == "__TEMP__":
            continue
        if sel_clean.startswith(name):
            return name
    return None

# ----- Jellyfin API and FZF -----
def jellyfin_auth(base_url, username, password, device='jellyfin-fzf-music'):
    headers = {
        "X-Emby-Authorization": f'MediaBrowser Client="JellyfinFZF", Device="{device}", DeviceId="fzf-{username}", Version="1.0.0"',
        "Content-Type": "application/json"
    }
    data = {"Username": username, "Pw": password}
    url = urljoin(base_url + '/', "Users/AuthenticateByName")
    r = requests.post(url, headers=headers, json=data, verify=False)
    r.raise_for_status()
    token = r.json()['AccessToken']
    user_id = r.json()['User']['Id']
    return token, user_id

def get_music_items(base_url, token, user_id, album_id=None):
    headers = {"X-Emby-Token": token}
    params = {
        "IncludeItemTypes": "Audio",
        "Recursive": "true",
        "Fields": "AlbumArtist,Album,Artist,Artists",
        "SortBy": "Album,SortName",
        "SortOrder": "Ascending",
        "UserId": user_id
    }
    if album_id:
        params["ParentId"] = album_id
    url = urljoin(base_url + '/', f"Users/{user_id}/Items")
    r = requests.get(url, headers=headers, params=params, verify=False)
    r.raise_for_status()
    return r.json()['Items']

def get_all_songs(base_url, token, user_id):
    headers = {"X-Emby-Token": token}
    params = {
        "IncludeItemTypes": "Audio",
        "Recursive": "true",
        "Fields": "AlbumArtist,Album,Artist,Artists",
        "SortBy": "Name,SortName",
        "SortOrder": "Ascending",
        "UserId": user_id
    }
    url = urljoin(base_url + '/', f"Users/{user_id}/Items")
    r = requests.get(url, headers=headers, params=params, verify=False)
    r.raise_for_status()
    return r.json()['Items']

def get_albums(base_url, token, user_id):
    headers = {"X-Emby-Token": token}
    params = {
        "IncludeItemTypes": "MusicAlbum",
        "Recursive": "true",
        "Fields": "AlbumArtist,Album",
        "SortBy": "Album,SortName",
        "SortOrder": "Ascending",
        "UserId": user_id
    }
    url = urljoin(base_url + '/', f"Users/{user_id}/Items")
    r = requests.get(url, headers=headers, params=params, verify=False)
    r.raise_for_status()
    return r.json()['Items']

def get_artists(base_url, token, user_id):
    headers = {"X-Emby-Token": token}
    params = {
        "IncludeItemTypes": "MusicArtist",
        "Recursive": "true",
        "SortBy": "SortName",
        "SortOrder": "Ascending",
        "UserId": user_id
    }
    url = urljoin(base_url + '/', f"Users/{user_id}/Items")
    r = requests.get(url, headers=headers, params=params, verify=False)
    r.raise_for_status()
    return r.json()['Items']

def get_artist_albums(base_url, token, user_id, artist_id):
    headers = {"X-Emby-Token": token}
    params = {
        "IncludeItemTypes": "MusicAlbum",
        "Recursive": "true",
        "Fields": "AlbumArtist,Album",
        "SortBy": "Album,SortName",
        "SortOrder": "Ascending",
        "UserId": user_id,
        "ArtistIds": artist_id
    }
    url = urljoin(base_url + '/', f"Users/{user_id}/Items")
    r = requests.get(url, headers=headers, params=params, verify=False)
    r.raise_for_status()
    return r.json()['Items']

def get_genres(base_url, token, user_id):
    headers = {"X-Emby-Token": token}
    params = {
        "IncludeItemTypes": "MusicAlbum,Audio",
        "Recursive": "true",
        "SortBy": "SortName",
        "SortOrder": "Ascending",
        "UserId": user_id
    }
    url = urljoin(base_url + '/', "MusicGenres")
    r = requests.get(url, headers=headers, params=params, verify=False)
    r.raise_for_status()
    return r.json()['Items']

def search_music_items(base_url, token, user_id, artist_id=None, genre_id=None):
    headers = {"X-Emby-Token": token}
    params = {
        "IncludeItemTypes": "Audio",
        "Recursive": "true",
        "Fields": "AlbumArtist,Album,Artist,Artists,Genres",
        "SortBy": "Album,SortName",
        "SortOrder": "Ascending",
        "UserId": user_id
    }
    
    if artist_id:
        params["ArtistIds"] = artist_id
    if genre_id:
        params["GenreIds"] = genre_id
        
    url = urljoin(base_url + '/', f"Users/{user_id}/Items")
    r = requests.get(url, headers=headers, params=params, verify=False)
    r.raise_for_status()
    return r.json()['Items']

def get_stream_url(base_url, token, user_id, item_id):
    return (f"{base_url}/Audio/{item_id}/stream"
            f"?UserId={user_id}&api_key={token}"
            f"&container=mp3"
            f"&audioCodec=mp3"
            f"&transcodingContainer=mp3"
            f"&transcodingProtocol=ffmpeg"
            f"&maxAudioChannels=2"
            f"&audioBitRate=192000"
            f"&static=true")

def fzf_select(entries, preview_cmd=None, multi=False, prompt=None):
    try:
        fzf_cmd = ['fzf', '--ansi', '--height=40%', '--border']
        if multi:
            fzf_cmd.append('--multi')
        if preview_cmd:
            fzf_cmd += ['--preview', preview_cmd]
        if prompt:
            fzf_cmd += ['--prompt', prompt]
        p = subprocess.Popen(
            fzf_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True
        )
        stdout, _ = p.communicate('\n'.join(entries))
        return [line.strip() for line in stdout.strip().split('\n') if line.strip()]
    except KeyboardInterrupt:
        sys.exit(0)

def search_by_song(base_url, token, user_id):
    print(color("Fetching all songs...", Color.FG_CYAN))
    songs = get_all_songs(base_url, token, user_id)
    if not songs:
        print(color("No songs found.", Color.WARNING))
        return None

    song_choices = []
    for s in songs:
        # Handle artist name more safely
        artist_name = 'Unknown Artist'
        if s.get('Artists') and isinstance(s.get('Artists'), list) and s['Artists']:
            artist_name = s['Artists'][0]
        elif s.get('AlbumArtist') and isinstance(s.get('AlbumArtist'), list) and s['AlbumArtist']:
            artist_name = s['AlbumArtist'][0]
        elif s.get('Artist'):
            artist_name = s['Artist']

        song_choices.append(
            color(s.get('Name', 'Unknown Title'), Color.FG_GREEN + Color.BOLD) +
            color(" - ", Color.FG_WHITE) +
            color(artist_name, Color.FG_YELLOW + Color.BOLD) +
            color(" [" + s.get('Album', 'Unknown Album') + "]", Color.FG_BLUE)
        )
    
    sel = fzf_select(song_choices, multi=True, prompt=color("Search Songs > ", Color.FG_CYAN + Color.BOLD))
    if not sel:
        return None

    selected_songs = []
    for selected in sel:
        for song in songs:
            # Handle artist name safely again for comparison
            artist_name = 'Unknown Artist'
            if song.get('Artists') and isinstance(song.get('Artists'), list) and song['Artists']:
                artist_name = song['Artists'][0]
            elif song.get('AlbumArtist') and isinstance(song.get('AlbumArtist'), list) and song['AlbumArtist']:
                artist_name = song['AlbumArtist'][0]
            elif song.get('Artist'):
                artist_name = song['Artist']

            song_display = (
                color(song.get('Name', 'Unknown Title'), Color.FG_GREEN + Color.BOLD) +
                color(" - ", Color.FG_WHITE) +
                color(artist_name, Color.FG_YELLOW + Color.BOLD) +
                color(" [" + song.get('Album', 'Unknown Album') + "]", Color.FG_BLUE)
            )
            if strip_ansi(selected) == strip_ansi(song_display):
                selected_songs.append(song)

    return selected_songs

def search_by_artist(base_url, token, user_id):
    print(color("Fetching artists...", Color.FG_CYAN))
    artists = get_artists(base_url, token, user_id)
    if not artists:
        print(color("No artists found.", Color.WARNING))
        return None, None

    artist_choices = [
        color(a.get('Name', 'Unknown Artist'), Color.FG_YELLOW + Color.BOLD)
        for a in artists
    ]
    sel = fzf_select(artist_choices, prompt=color("Select Artist > ", Color.FG_CYAN + Color.BOLD))
    if not sel:
        return None, None
        
    selected_artist = None
    for i, a in enumerate(artists):
        if strip_ansi(artist_choices[i]) == strip_ansi(sel[0]):
            selected_artist = a
            break
    
    if not selected_artist:
        return None, None

    # Get albums for the selected artist
    print(color(f"Fetching albums for {selected_artist.get('Name')}...", Color.FG_CYAN))
    albums = get_artist_albums(base_url, token, user_id, selected_artist['Id'])
    
    if not albums:
        print(color("No albums found for this artist.", Color.WARNING))
        return selected_artist['Id'], None

    album_choices = [
        color("All Songs", Color.FG_MAGENTA + Color.BOLD)  # Option to view all songs
    ] + [
        color(a.get('Name', 'Unknown Album'), Color.FG_GREEN + Color.BOLD) +
        color(" [" + str(a.get('ProductionYear', '')) + "]", Color.FG_BLUE)
        for a in albums
    ]
    
    sel = fzf_select(album_choices, prompt=color("Select Album > ", Color.FG_CYAN + Color.BOLD))
    if not sel:
        return None, None

    if strip_ansi(sel[0]) == "All Songs":
        return selected_artist['Id'], None

    # Find selected album
    for album in albums:
        album_display = color(album.get('Name', 'Unknown Album'), Color.FG_GREEN + Color.BOLD) + \
                       color(" [" + str(album.get('ProductionYear', '')) + "]", Color.FG_BLUE)
        if strip_ansi(album_display) == strip_ansi(sel[0]):
            return selected_artist['Id'], album['Id']
    
    return None, None

def search_by_genre(base_url, token, user_id):
    print(color("Fetching genres...", Color.FG_CYAN))
    genres = get_genres(base_url, token, user_id)
    if not genres:
        print(color("No genres found.", Color.WARNING))
        return None

    genre_choices = [
        color(g.get('Name', 'Unknown Genre'), Color.FG_GREEN + Color.BOLD)
        for g in genres
    ]
    sel = fzf_select(genre_choices, prompt=color("Select Genre > ", Color.FG_CYAN + Color.BOLD))
    if not sel:
        return None
        
    for i, g in enumerate(genres):
        if strip_ansi(genre_choices[i]) == strip_ansi(sel[0]):
            return g['Id']
    return None

def playback_menu_fzf():
    options = [
        color("Pause", Color.FG_YELLOW),
        color("Resume", Color.FG_GREEN),
        color("Next", Color.FG_CYAN),
        color("Back to Album", Color.FG_MAGENTA),
        color("Main Menu", Color.FG_BLUE),
        color("Quit", Color.FG_RED + Color.BOLD),
    ]
    selected = fzf_select(options, prompt=color("Playback Command > ", Color.FG_CYAN + Color.BOLD))
    if not selected:
        return None
    clean = strip_ansi(selected[0]).lower()
    return clean

def main_menu_fzf():
    options = [
        color("Browse Albums", Color.FG_CYAN + Color.BOLD),
        color("Search by Artist", Color.FG_YELLOW + Color.BOLD),
        color("Search by Genre", Color.FG_GREEN + Color.BOLD),
        color("Search by Song", Color.FG_MAGENTA + Color.BOLD),
        color("Change Server", Color.FG_BLUE + Color.BOLD),
        color("Quit", Color.FG_RED + Color.BOLD),
    ]
    selected = fzf_select(options, prompt=color("Main Menu > ", Color.FG_MAGENTA + Color.BOLD))
    if not selected:
        return None
    clean = strip_ansi(selected[0]).lower()
    return clean

def play_with_ffmpeg_interactive(url, song_title, song_artist):
    try:
        ffmpeg_cmd = [
            'ffmpeg',
            '-user_agent', 'JellyfinFZF/1.0.0',
            '-headers', 'Accept: */*\r\n',
            '-i', url,
            '-vn',
            '-f', 'wav',
            '-'
        ]
        ffplay_cmd = [
            'ffplay',
            '-autoexit',
            '-nodisp',
            '-loglevel', 'quiet',
            '-'
        ]
        print(
            color("\n▶ Now Playing: ", Color.FG_GREEN + Color.BOLD) +
            color(song_title, Color.FG_YELLOW + Color.BOLD) +
            color(" - ", Color.FG_WHITE) +
            color(song_artist, Color.FG_MAGENTA + Color.BOLD) + "\n"
        )

        ffmpeg = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        ffplay = subprocess.Popen(ffplay_cmd, stdin=ffmpeg.stdout, preexec_fn=os.setsid)
        ffmpeg.stdout.close()

        while True:
            if ffplay.poll() is not None:
                break
            cmd = playback_menu_fzf()
            if cmd is None:
                continue
            if cmd.startswith('pause'):
                os.killpg(os.getpgid(ffplay.pid), signal.SIGSTOP)
                print(color("Paused.", Color.WARNING))
            elif cmd.startswith('resume'):
                os.killpg(os.getpgid(ffplay.pid), signal.SIGCONT)
                print(color("Resumed.", Color.OKGREEN))
            elif cmd.startswith('next'):
                os.killpg(os.getpgid(ffplay.pid), signal.SIGTERM)
                ffplay.wait()
                ffmpeg.terminate()
                return 'next'
            elif cmd.startswith('back'):
                os.killpg(os.getpgid(ffplay.pid), signal.SIGTERM)
                ffplay.wait()
                ffmpeg.terminate()
                return 'back_album'
            elif cmd.startswith('main'):
                os.killpg(os.getpgid(ffplay.pid), signal.SIGTERM)
                ffplay.wait()
                ffmpeg.terminate()
                return 'main_menu'
            elif cmd.startswith('quit'):
                try:
                    os.killpg(os.getpgid(ffplay.pid), signal.SIGTERM)
                except Exception:
                    pass
                try:
                    ffplay.wait(timeout=2)
                except Exception:
                    pass
                try:
                    ffmpeg.terminate()
                    ffmpeg.wait(timeout=2)
                except Exception:
                    pass
                print(color("Goodbye!", Color.FG_MAGENTA + Color.BOLD))
                restore_terminal()
                return 'quit'
            else:
                print(color("Unknown option.", Color.WARNING))

        ffmpeg.terminate()
        return 'finished'
    except Exception as e:
        print(color(f"Error playing stream: {e}", Color.FAIL))
        return 'error'

def main():
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    print(color("jly-fin a cli jellyfin music player", Color.FG_MAGENTA + Color.BOLD))

    servers = load_servers()
    while True:
        if not servers or all(k == "__TEMP__" for k in servers):
            print(color("No servers configured, please add one.", Color.WARNING))
            add_server_interactive(servers)
            servers = load_servers()
            continue
            
        server_choice = choose_server_fzf(servers)
        if server_choice is None:
            print(color("No server chosen, exiting.", Color.FAIL))
            return
        if server_choice == 'add':
            server_choice = add_server_interactive(servers)
            servers = load_servers()
        
        server = servers[server_choice]
        base_url = server["url"]
        username = server["username"]
        password = server["password"]

        print(color(f"Logging in to {server_choice if server_choice != '__TEMP__' else 'temporary server'}...", Color.FG_BLUE))
        try:
            token, user_id = jellyfin_auth(base_url, username, password)
        except Exception as e:
            print(color(f"Login failed: {e}", Color.FAIL))
            if server_choice == "__TEMP__":
                servers.pop("__TEMP__", None)
            continue

        while True:  # Main menu loop
            menu_choice = main_menu_fzf()
            if menu_choice is None or menu_choice.startswith('quit'):
                if server_choice == "__TEMP__":
                    servers.pop("__TEMP__", None)
                restore_terminal()
                print(color("Goodbye!", Color.FG_MAGENTA + Color.BOLD))
                return
            elif menu_choice.startswith('change'):
                break

            songs = None
            if menu_choice.startswith('search by artist'):
                artist_id, album_id = search_by_artist(base_url, token, user_id)
                if artist_id:
                    if album_id:
                        songs = get_music_items(base_url, token, user_id, album_id=album_id)
                    else:
                        songs = search_music_items(base_url, token, user_id, artist_id=artist_id)
            elif menu_choice.startswith('search by genre'):
                genre_id = search_by_genre(base_url, token, user_id)
                if genre_id:
                    songs = search_music_items(base_url, token, user_id, genre_id=genre_id)
            elif menu_choice.startswith('search by song'):
                songs = search_by_song(base_url, token, user_id)
            elif menu_choice.startswith('browse'):
                print(color("Fetching albums...", Color.FG_CYAN))
                albums = get_albums(base_url, token, user_id)
                if not albums:
                    print(color("No albums found.", Color.WARNING))
                    continue

                album_choices = [
                    color(a.get('AlbumArtist', ['Unknown Artist'])[0] if isinstance(a.get('AlbumArtist', []), list)
                          else a.get('AlbumArtist', 'Unknown Artist'), Color.FG_YELLOW + Color.BOLD) +
                    color(" - ", Color.FG_WHITE) +
                    color(a.get('Name', 'Unknown Album'), Color.FG_GREEN + Color.BOLD)
                    for a in albums
                ]
                sel = fzf_select(album_choices, prompt=color("Select Album > ", Color.FG_CYAN + Color.BOLD))
                if not sel:
                    print(color("No album selected.", Color.WARNING))
                    continue
                    
                album_id = None
                for i, a in enumerate(albums):
                    if strip_ansi(album_choices[i]) == strip_ansi(sel[0]):
                        album_id = a['Id']
                        break
                        
                if album_id:
                    songs = get_music_items(base_url, token, user_id, album_id=album_id)

            if songs:
                last_menu_choice = menu_choice
                last_artist_id = artist_id if 'artist_id' in locals() else None
                last_album_id = album_id if 'album_id' in locals() else None
                last_genre_id = genre_id if 'genre_id' in locals() else None

                if not songs:
                    print(color("No songs found.", Color.WARNING))
                    continue
                    
                song_choices = [
                    color(f"{s.get('IndexNumber', '?'):02d}.", Color.FG_MAGENTA + Color.BOLD) +
                    color(" ", Color.FG_WHITE) +
                    color(s.get('Name', 'Unknown Title'), Color.FG_GREEN + Color.BOLD) +
                    color(" - ", Color.FG_WHITE) +
                    color(s.get('Artists', [s.get('AlbumArtist', ['Unknown Artist'])[0]])[0] 
                          if isinstance(s.get('Artists', []), list) or isinstance(s.get('AlbumArtist', []), list)
                          else (s.get('Artist') or s.get('AlbumArtist', 'Unknown Artist')),
                          Color.FG_YELLOW + Color.BOLD) +
                    color(" [" + s.get('Album', 'Unknown Album') + "]", Color.FG_BLUE)
                    for s in songs
                ]
                
                while True:  # Song selection loop
                    sel = fzf_select(song_choices, multi=True, prompt=color("Select Tracks > ", Color.FG_CYAN + Color.BOLD))
                    if not sel:
                        break  # Break to main menu if no selection

                    song_map = {
                        (s.get('IndexNumber', '?'),
                         s.get('Name', 'Unknown Title'),
                         s.get('Artists', [s.get('AlbumArtist', ['Unknown Artist'])[0]])[0]
                         if isinstance(s.get('Artists', []), list) or isinstance(s.get('AlbumArtist', []), list)
                         else (s.get('Artist') or s.get('AlbumArtist', 'Unknown Artist'))
                        ): s for s in songs
                    }

                    selected_tuples = []
                    for s in sel:
                        clean = strip_ansi(s)
                        try:
                            idx_and_rest = clean.split(". ", 1)
                            index_number = int(idx_and_rest[0])
                            rest = idx_and_rest[1]
                            name_and_rest = rest.split(" - ", 1)
                            name = name_and_rest[0]
                            artist_and_album = name_and_rest[1].split(" [", 1)
                            artist = artist_and_album[0]
                            selected_tuples.append((index_number, name, artist))
                        except Exception:
                            continue

                    current_index = 0
                    while current_index < len(selected_tuples):
                        sel_tuple = selected_tuples[current_index]
                        song_obj = song_map.get(sel_tuple)
                        if not song_obj:
                            current_index += 1
                            continue
                            
                        song_title = song_obj.get('Name', 'Unknown Title')
                        song_artist = (
                            song_obj.get('Artists', [song_obj.get('AlbumArtist', ['Unknown Artist'])[0]])[0]
                            if isinstance(song_obj.get('Artists', []), list) or isinstance(song_obj.get('AlbumArtist', []), list)
                            else (song_obj.get('Artist') or song_obj.get('AlbumArtist', 'Unknown Artist'))
                        )
                        
                        url = get_stream_url(base_url, token, user_id, song_obj['Id'])
                        result = play_with_ffmpeg_interactive(url, song_title, song_artist)
                        
                        if result == 'quit':
                            if server_choice == "__TEMP__":
                                servers.pop("__TEMP__", None)
                            restore_terminal()
                            return
                        elif result == 'next':
                            current_index += 1
                        elif result == 'back_album':
                            songs = None  # Clear current songs
                            # Re-fetch songs based on last context
                            if last_menu_choice.startswith('search by artist'):
                                if last_album_id:
                                    songs = get_music_items(base_url, token, user_id, album_id=last_album_id)
                                elif last_artist_id:
                                    songs = search_music_items(base_url, token, user_id, artist_id=last_artist_id)
                            elif last_menu_choice.startswith('search by genre') and last_genre_id:
                                songs = search_music_items(base_url, token, user_id, genre_id=last_genre_id)
                            elif last_menu_choice.startswith('search by song'):
                                songs = None  # Return to song search
                            break  # Break to song selection
                        elif result == 'main_menu':
                            songs = None
                            break  # Break to song selection, which will then break to main menu
                        elif result == 'finished':
                            current_index += 1
                        elif result == 'error':
                            current_index += 1
                    
                    if not songs:
                        break  # Break to main menu if songs were cleared

if __name__ == "__main__":
    main()
