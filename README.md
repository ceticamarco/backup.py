# backup.sh ![](https://git.marcocetica.com/marco/backup.sh/actions/workflows/backup.sh.yml/badge.svg)

`backup.sh` is a POSIX compliant, modular and lightweight backup utility to save and encrypt your files.
This tool is intended to be used on small scale UNIX environments such as VPS, personal servers and
workstations. `backup.sh` uses [rsync](https://linux.die.net/man/1/rsync), [tar](https://linux.die.net/man/1/tar), 
[gpg](https://linux.die.net/man/1/gpg) and [sha256sum](https://linux.die.net/man/1/sha256sum) 
to copy, compress, encrypt the backup and verify the backup.  

## Installation
`backup.sh` is a single source file, to install it you can copy the script wherever you want. Alternatively, if you 
are running a DEB/RPM distribution, you can install it with the following command:
```sh 
$> sudo apt install ./bin/backup.sh-1.0.0.x86_64.deb # Debian
$> sudo dnf install ./bin/backup.sh-1.0.0-2.x86_64.rpm # RHEL
```

For any other UNIX system, you can use the following command:
```sh
$> sudo make install
```
This will copy `backup.sh` into `/usr/local/bin/backup.sh`, `sources.bk` into `/usr/local/etc/sources.bk` and
`backup.sh.1` into `/usr/share/man/man1/backup.sh.1`. To uninstall the program along with the sample _sources file_ and the manual page, you can issue `sudo make uninstall`.

At this point you still need to install the following dependencies:
- `Bash(v>=4)`
- `rsync`
- `tar`
- `gpg`

## Usage
To show the available options, you can run `backup.sh --help`, which will print out the following message:
```text
backup.sh v1.0.0 - POSIX compliant, modular and lightweight backup utility.

Syntax: ./backup.sh [-b|-e|-c|-V|-h]
options:
-b|--backup   SOURCES DEST PASS  Backup folders from SOURCES file.
-e|--extract  ARCHIVE PASS       Extract ARCHIVE using PASS.
-c|--checksum                    Generate/check SHA256 of a backup.
-V|--verbose                     Enable verbose mode.
-h|--help                        Show this helper.

General help with the software: https://github.com/ceticamarco/backup.sh
Report bugs to: Marco Cetica(<email@marcocetica.com>)
```

As you can see, `backup.sh` supports three options: **backup creation**, **backup extraction** and **checksum** to verify the
integrity of a backup. The first option requires
root permissions, while the second one does not. The checksum option must be used in combination of one of the previous options.

### Backup creation
To specify the directories to back up, `backup.sh` uses an associative array
defined in a text file(called _sources file_) with the following syntax:

```text
<LABEL>=<PATH>
```

Where `<LABEL>` is the name of the backup and `<PATH>` is its path. For example,
if you want to back up `/etc/nginx` and `/etc/ssh`, add the following entries to the _sources file_:

```text
nginx=/etc/nginx/
ssh=/etc/ssh/
```

`backup.sh` will create two folders inside the backup archive with the following syntax:
```text
backup-<LABEL>-<YYYYMMDD>
```

In the previous example, this would be:
```text
backup-nginx-<YYYYMMDD>
backup-ssh-<YYYYMMDD>
```

You can add as many entries as you want, just be sure to use the proper syntax. In particular,
the _sources file_, **should not** include:
- Spaces between the label and the equal sign;  
- Empty lines;  
- Comments.

You can find a sample _sources file_ at `sources.bk`(or at `/usr/local/etc/sources.bk`).

After having defined the _sources file_, you can invoke `backup.sh` using the following syntax:
```sh
$> sudo ./backup.sh --backup <SOURCES_FILE> <DEST> <ENCRYPTION_PASSWORD>
```

Where `<SOURCES_FILE>` is the _sources file_, `<DEST>` is the absolute path of the output of the backup 
**without trailing slashes** and `<ENCRYPTION_PASSWORD>` is the password to encrypt the compressed archive.

In the previous example, this would be:
```sh
$> sudo ./backup.sh --backup sources.bk /home/john badpw1234
```

You can also tell `backup.sh` to generate a SHA256 file containing the hash of each file using the `-c` option. 
In the previous example, this would be:
```sh
$> sudo ./backup.sh --checksum --backup sources.bk /home/john badpw1234
```

The backup utility will begin to copy the files defined in the _sources file_:
```text
Copying nginx(1/2)
Copying ssh(2/2)
Compressing backup...
Encrypting backup...
File name: /home/john/backup-<HOSTNAME>-<YYYYMMDD>.tar.gz.enc
Checksum file: /home/john/backup-<HOSTNAME>-<YYYYMMDD>.sha256
File size: 7336400696(6.9G)
Elapsed time: 259 seconds.
```

After that, you will find the backup archive and the checksum file in 
`/home/john/backup-<HOSTNAME>-<YYYYMMDD>.tar.gz.enc` and `/home/john/backup-<HOSTNAME>-<YYYYMMDD>.sha256`, respectively.

You can also use `backup.sh` from a crontab rule:
```sh
$> sudo crontab -e
30 03 * * 6 EKEY=$(cat /home/john/.ekey) bash -c '/usr/local/bin/backup.sh -b /usr/local/etc/sources.bk /home/john $EKEY' > /dev/null 2>&1

```

This will automatically run `backup.sh` every Saturday morning at 03:30 AM. In the example above, the encryption
key is stored in a local file(with fixed permissions) to avoid password leaking in crontab logs. You can also
adopt this practice while using the `--extract` option to avoid password leaking in shell history.

By default `backup.sh` is very quiet, to add some verbosity to the output, be sure to use the `-V`(`--verbose`) option.

### Backup extraction
`backup.sh` can also be used to extract and to verify the encrypted backup.   
To do so, use the following commands:

```sh
$> ./backup.sh --extract <ENCRYPTED_ARCHIVE> <ARCHIVE_PASSWORD>
```

Where `<ENCRYPTED_ARCHIVE>` is the encrypted backup and `<ARCHIVE_PASSWORD>` is the backup password.

For instance:

```sh
$> ./backup.sh --extract backup-<hostname>-<YYYYMMDD>.tar.gz.enc badpw1234
```

This will create a new folder called `backup.sh.tmp` in your local directory with the following content: 
```text
backup-nginx-<YYYYMMDD>
backup-ssh-<YYYYMMDD>
```

**note**: be sure to rename any directory with that name to avoid collisions.


If you also want to verify the integrity of the backup data, use the following commands:
```sh
$> ./backup.sh --checksum --extract <ENCRYPTED_ARCHIVE> <ARCHIVE_PASSWORD> <CHECKSUM_ABSOLUTE_PATH>
```

For instance:

```sh
$> ./backup.sh --checksum --extract backup-<hostname>-<YYYYMMDD>.tar.gz.enc badpw1234 backup-<hostname>-<YYYYMMDD>.sha256
```

## How does backup.sh work?
**backup.sh** uses _rsync_ to copy the files, _tar_ to compress the backup, _gpg_ to encrypt it and
_sha256sum_ to verify it.
By default, rsync is being used with the following parameters:

```
$> rsync -aPhrq --delete
```

That is:

    - a: archive mode: rsync copies files recursively while preserving as much metadata as possible;  
    - P: progress/partial: allows rsync to resume interrupted transfers and to shows progress information;  
    - h: human readable output, rsync shows output numbers in a more readable way;  
    - r: recursive mode: forces rsync to copy directories and their content;  
    - q: quiet mode: reduces the amount of information rsync produces;  
    - delete: delete mode: forces rsync to delete any extraneous files at the destination dir.

If specified(`--checksum` option), `backup.sh` can also generate the checksum of each file of the backup.
To do so, it uses `sha256sum(1)` to compute the hash of every single file using the SHA256 hashing algorithm.
The checksum file contains nothing but the checksums of the files, no other information about the files stored
on the backup archive is exposed on the unencrypted checksum file. This may be an issue if you want plausible
deniability(see privacy section for more information).


After that the backup folder is being encrypted using gpg. By default, it is used with the following parameters:

```
$> gpg -a \
        --symmetric \
        --cipher-algo=AES256 \
        --no-symkey-cache \
        --pinentry-mode=loopback \
        --batch --passphrase "$PASSWORD" \
        --output "$OUTPUT" \
        "$INPUT"
```

This command encrypts the backup using the AES-256 symmetric encryption algorithm with a 256bit key. Here is what each flag do:
 - `--symmetric`: Use symmetric encryption;  
 - `--cipher-algo=AES256`: Use AES256 algorithm;  
 - `--no-symkey-cache`: Do not save password on GPG's cache;  
 - `--pinentry-mode=loopback --batch`: Do not prompt the user;  
 - `--passphrase-fd 3 3<< "$PASSWORD"`: Read password without revealing it on `ps`;  
 - `--output`: Specify output file;  
 - `$INPUT`: Specify input file.

## Plausible Deniability
While `backup.sh` provide some pretty strong security against bruteforce attack(assuming a strong passphrase is being used)
it should by no means considered a viable tool against a cryptanalysis investigation. Many of the copying, compressing and
encrypting operations made by `backup.sh` during the backup process can be used to invalidate plausible deniability.
In particular, you should pay attention to the following details:

1. The `--checksum` option generates an **UNENCRYPTED** checksum file containing the _digests_ of **EVERY**
file in your backup archive. If your files are known to your adversary(e.g., a banned book), they may use a rainbow table attack to
determine whether you own a given file, voiding your plausible deniability;  
2. Since `backup.sh` is essentially a set of shell commands, an eavesdropper could monitor the whole backup process to extract
the name of the files or the encryption password.

## Unit tests
`backup.sh` provides some unit tests inside the `tests.sh` script. This script generates some dummy files inside the following
directories:
- /var/log
- /var/www
- /etc/nginx
- /etc/ssh

For this reason, this script should **NOT** be used in non-testing environments. To run all tests, issue the following command:
```sh
$> sudo ./tests.sh I_HAVE_READ_THE_HELPER
```

## License
This software is released under GPLv3, you can obtain a copy of this license by cloning this repository or by visiting 
[this page](https://choosealicense.com/licenses/gpl-3.0/).
