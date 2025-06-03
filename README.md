# jly-cli

A terminal-based Jellyfin music browser and player using `fzf` inspired by ani-cli.

## Features

- Browse albums and songs from a Jellyfin server.
- Stream music using `ffmpeg`
- Support for multiple Jellyfin servers.
- Search by album, artist, genere, or song
  
## Planned Features

- Some kind of queue system.
- Lyric support

## Requirements

- Python 3
- `fzf`
- `ffmpeg` and `ffplay`
- `requests`

## Usage

```bash
./jly-cli
```

## Instalation

```bash
sudo curl -L https://raw.githubusercontent.com/nixietab/jly-cli/refs/heads/main/jly-cli -o /usr/bin/jly-cli 
sudo chmod +x /usr/bin/jly-cli
```
