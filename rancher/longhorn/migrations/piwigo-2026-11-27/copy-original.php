<?php
// Runs only in the inspection pod, with original data mounted ro,norecovery.
// No shell commands or database processes are started by this worker.
declare(strict_types=1);
umask(0077);
$limit = 1048576;
$current = -1;
$bytes = 0;
$files = 0;
$lastProgress = 0.0;
$manifest = [];

function event(string $event, array $fields = []): void {
    echo json_encode(['time' => gmdate('c'), 'event' => $event] + $fields, JSON_THROW_ON_ERROR)."\n";
    flush();
}

function pressure(): array {
    $text = file_get_contents('/proc/pressure/io');
    if ($text === false) { throw new RuntimeException('Cannot read I/O pressure'); }
    $result = [];
    foreach (explode("\n", trim($text)) as $line) {
        if (preg_match('/^(some|full) avg10=([0-9.]+)/', $line, $m)) {
            $result[$m[1]] = (float)$m[2];
        }
    }
    if (count($result) !== 2) { throw new RuntimeException('Incomplete I/O pressure'); }
    return $result;
}

function changeCap(int $new, string $reason, array $psi): void {
    global $current;
    if ($new === $current) { return; }
    $fields = ['previousBytesPerSecond' => $current < 0 ? null : $current,
               'newBytesPerSecond' => $new, 'reason' => $reason, 'ioPsiAvg10' => $psi];
    event('CAP_REQUEST', $fields);
    $current = $new;
    event('CAP_CONFIRMED', $fields + ['liveWorkerApplied' => true]);
}

function pace(int $size): void {
    global $limit, $current;
    $pausedSince = null;
    while (true) {
        $psi = pressure();
        $unsafe = $psi['some'] > 5.0 || $psi['full'] > 1.0;
        // Lower resume threshold avoids repeatedly switching around the pause threshold.
        if ($current === 0) { $unsafe = $psi['some'] > 1.0 || $psi['full'] > 0.2; }
        if (!$unsafe) {
            changeCap($limit, $current === 0 ? 'I/O pressure settled; resume conservative cap' : 'Initial conservative copy/read-verification cap', $psi);
            break;
        }
        changeCap(0, 'Automatic safety pause: elevated I/O pressure', $psi);
        $pausedSince ??= microtime(true);
        if (microtime(true) - $pausedSince > 300) {
            throw new RuntimeException('I/O safety pause exceeded five minutes; partial scratch copy retained');
        }
        usleep(1000000);
    }
    usleep((int)ceil($size / $limit * 1000000));
}

function progress(): void {
    global $bytes, $files, $lastProgress;
    if (microtime(true) - $lastProgress >= 5) {
        event('PROGRESS', ['readBytesIncludingVerification' => $bytes, 'filesCopied' => $files,
                           'ioPsiAvg10' => pressure()]);
        $lastProgress = microtime(true);
    }
}

function checkMount(): void {
    $mounts = file('/proc/mounts', FILE_IGNORE_NEW_LINES);
    foreach ($mounts as $line) {
        $parts = explode(' ', $line);
        if ($parts[1] === '/source') {
            $flags = explode(',', $parts[3]);
            if ($parts[2] !== 'ext4' || !in_array('ro', $flags, true) ||
                (!in_array('norecovery', $flags, true) && !in_array('noload', $flags, true))) {
                throw new RuntimeException('Source is not ext4 ro,norecovery');
            }
            return;
        }
    }
    throw new RuntimeException('Source mount missing');
}

function metadata(string $destination, array $stat): void {
    if (!chown($destination, $stat['uid']) || !chgrp($destination, $stat['gid']) ||
        !chmod($destination, $stat['mode'] & 07777) || !touch($destination, $stat['mtime'])) {
        throw new RuntimeException('Cannot preserve file metadata');
    }
}

function copyTree(string $source, string $destination, string $relative): void {
    global $bytes, $files, $manifest;
    $stat = lstat($source);
    if ($stat === false || is_link($source)) { throw new RuntimeException('Unreadable source or unexpected symlink'); }
    if (is_dir($source)) {
        if (!mkdir($destination, 0700)) { throw new RuntimeException('Cannot create scratch directory'); }
        $entries = scandir($source);
        if ($entries === false) { throw new RuntimeException('Cannot enumerate source directory'); }
        foreach ($entries as $name) {
            if ($name !== '.' && $name !== '..') { copyTree($source.'/'.$name, $destination.'/'.$name, $relative.'/'.$name); }
        }
        metadata($destination, $stat);
        return;
    }
    if (!is_file($source)) { throw new RuntimeException('Unexpected non-regular source entry'); }
    $input = fopen($source, 'rb');
    $output = fopen($destination, 'xb');
    if ($input === false || $output === false) { throw new RuntimeException('Cannot open source or exclusive destination'); }
    $hash = hash_init('sha256');
    $size = 0;
    while (!feof($input)) {
        pace(262144);
        $chunk = fread($input, 262144);
        if ($chunk === false) { throw new RuntimeException('Source read failed'); }
        if ($chunk === '') { break; }
        $length = strlen($chunk);
        hash_update($hash, $chunk);
        $written = 0;
        while ($written < $length) {
            $n = fwrite($output, substr($chunk, $written));
            if ($n === false || $n === 0) { throw new RuntimeException('Scratch write failed'); }
            $written += $n;
        }
        $size += $length;
        $bytes += $length;
        progress();
    }
    if (!fflush($output) || !fsync($output)) { throw new RuntimeException('Scratch sync failed'); }
    fclose($input);
    fclose($output);
    if ($size !== $stat['size']) { throw new RuntimeException('Source size changed or copy truncated'); }
    $expected = hash_final($hash);
    $verify = fopen($destination, 'rb');
    if ($verify === false) { throw new RuntimeException('Cannot reopen copied file'); }
    $hash = hash_init('sha256');
    while (!feof($verify)) {
        pace(262144);
        $chunk = fread($verify, 262144);
        if ($chunk === false) { throw new RuntimeException('Verification read failed'); }
        if ($chunk === '') { break; }
        hash_update($hash, $chunk);
        $bytes += strlen($chunk);
        progress();
    }
    fclose($verify);
    if (!hash_equals($expected, hash_final($hash))) { throw new RuntimeException('Copied file checksum mismatch'); }
    metadata($destination, $stat);
    $manifest[] = ['path' => $relative, 'size' => $size, 'sha256' => $expected];
    $files++;
}

try {
    checkMount();
    if (!is_dir('/source/data/piwigo') || file_exists('/scratch/data') || file_exists('/scratch/original-copy-complete.json')) {
        throw new RuntimeException('Expected source schema absent or scratch not empty; do not overwrite');
    }
    event('START', ['sourceReadOnly' => true, 'journalReplayDisabled' => true,
                    'source' => '/source/data', 'target' => '/scratch/data']);
    pace(0);
    copyTree('/source/data', '/scratch/data', 'data');
    $result = ['completedUTC' => gmdate('c'), 'files' => $manifest,
               'fileCount' => $files, 'readBytesIncludingVerification' => $bytes];
    $f = fopen('/scratch/original-copy-complete.json', 'xb');
    if ($f === false || fwrite($f, json_encode($result, JSON_THROW_ON_ERROR)) === false || !fflush($f) || !fsync($f)) {
        throw new RuntimeException('Cannot persist verification manifest');
    }
    fclose($f);
    event('COMPLETE', ['fileCount' => $files, 'readBytesIncludingVerification' => $bytes,
                       'allFilesSHA256Verified' => true, 'ioPsiAvg10' => pressure()]);
} catch (Throwable $e) {
    event('FAILED', ['message' => $e->getMessage(), 'partialScratchRetained' => true]);
    exit(1);
}
