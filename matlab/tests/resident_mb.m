function mb = resident_mb()
% RESIDENT_MB  Resident memory of this process in MB, from /proc on Linux; NaN elsewhere.
mb = NaN;
fid = fopen('/proc/self/statm', 'r');
if fid < 0
    return
end
pages = fscanf(fid, '%d', 2);
fclose(fid);
mb = pages(2) * 4096 / 2^20;
end
