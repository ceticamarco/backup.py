<div align="center">
    <h1>backup.py</h1>
    <h6><i>Modular and lightweight backup utility to save, encrypt and verify your personal data.</i></h6>
    <img src=".usage.gif" />
</div>
  
## Overview
`backup.py` supports two major options: `--backup`, to create a new backup and `--extract`
to extract an existing backup archive.

In order to create a backup file, you first need to create a *"sources file"* to specify
the absolute paths to backup. For instance:

```ini
# directories end with a slash...
photos=/home/marco/Pictures/
documents=/home/marco/Documents/
# while individual files do not
wireguard=/etc/wireguard/wg0.conf
```

Then, you can start the backup with the following command:

```sh
$ sudo ./backup.py --checksum --backup sources.ini $PWD "very_bad_pw"
Copying photos (1/3)
Copying documents (2/3)
Copying wireguard (3/3)
File name: '/home/marco/Projects/backup.py/backup-wood-20260122.tar.gz.enc'
Checksums file: '/home/marco/Projects/backup.py/backup-wood-20260122.sha256'
File size: 5533818904 bytes (5.15 GiB)
Elapsed time: 2 minutes, 12 seconds
```

The `--checsum` (optional) is used to generate a checksum file containing the hashes of each single of the backup.

To extract an existing backup, you can instead issue the following command:

```sh
$ ./backup.py -c --extract backup-wood-20260122.tar.gz.enc "very_bad_pw" backup-wood-20260122.sha256
Backup extracted to: '/home/marco/Projects/backup.py/backup.py.tmp'
Elapsed time: 1 minute, 3 seconds
```

This will create a new directory named `backup.py.tmp` on your local path. Just like before,
the `-c` option is optional.

## Usage
As stated before, `python.py` is built from scratch in modern Python (3.10+) without using 
any external dependency except for `tar` and `gpg`. 

The *sources file* follows an INI-like syntax structured using associative records between
labels and absolute paths. In order words:

```ini
<label>=<path>
```

where:
- `<label>` is a descriptive name of a backup entry;  
- `<path>` is the absolute path to a directory or a file.

For example:

```ini
# File 'server_backup.ini'
# List directories and files to backup
#
nginx=/etc/nginx/
ssh=/etc/ssh/
www=/var/www/html/

# no slash here ----v
host_file=/etc/hosts
```

As you can see, individual files are specified by omitting the trailing slash at the end
of the absolute path. Comments, on the other hand, are inserted using the `#` token. Blank
lines are ignored.

Internally, `backup.py` orchestrates several UNIX utilities to create backups. In particular,
it follows the procedure listed below:

1. **Copy phase**: uses Python `shutil.copytree()` to copy files while preserving metadata and
symlinks (without following them) and by ignoring special files;  
2. **Compression**: creates a gzip-compressed tar archive using GNU tar;  
3. **Encryption**: encrypts the archive with GPG using AES-256 symmetric encryption;  
4. **Checksum** (optional): computes SHA256 hashes for each file in the backup archive.

The backup process creates temporary files in `backup.py.tmp` and `backup.py.tar.gz`, which are
automatically cleaned up on completion or interruption (i.e., `C-c`).

## Old version
This implementation of `backup.py` is a porting of an old backup script originally written in Bash
that I developed back in 2018. While this new version should be compatible with old backup archives,
it may start to diverge at a certain point in the future. If you're experience incompatibilities and want
to revert to the original version, you can do so by visiting the 
[latest stable commit](https://git.marcocetica.com/marco/backup.py/src/commit/786c30ef14abe2056dfa5cb250b766db73ca71aa).

## License
This software is released under GPLv3. You can obtain a copy of this license by visiting [this page](https://choosealicense.com/licenses/gpl-3.0/).
