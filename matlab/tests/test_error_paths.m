% Errors leave no native memory behind. Octave's mexErrMsgIdAndTxt jumps out of the gateway
% with longjmp, which skips C++ destructors, so the gateway raises the error only after every
% C++ object of the call is gone. Each failed load below holds a 1 MB path string when the core
% throws; if the string outlived the call, 300 calls would leak 300 MB.
r = filly.Renderer();
scene = r.createScene();
path = [tempdir, repmat('a', 1, 2^20)];
calls = 300;
before = resident_mb();
for i = 1:calls
    expect_error(@() scene.load(path), 'filly:asset');
    expect_error(@() filly_mex('get', scene.Id, 'noSuchProperty'), 'filly:invalidArgument');
    expect_error(@() filly_mex('Scene.close', r.Id), 'filly:invalidArgument');
end
growth = resident_mb() - before;
if isnan(growth)
    fprintf('  resident memory not measured on this platform\n');
else
    fprintf('  resident memory after %d failed calls of each kind: %+.1f MB\n', calls, growth);
    assert(growth < 50, 'filly:test', 'failed calls leaked %.0f MB', growth);
end
% The gateway still works after the errors.
assert(~scene.closed);
close(r);
