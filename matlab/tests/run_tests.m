function failures = run_tests(pattern)
% RUN_TESTS  Run the script tests in this folder and report each result.
%   failures = run_tests() runs every test_*.m; run_tests('test_batch*') runs a subset.
%   Each script runs in its own function workspace, so that its filly objects are deleted when
%   it ends, and the run checks that no gateway handles remain. The PTB tests are in ptb/ and
%   need a display (see ptb/run_ptb_tests.m).
%
%   From a shell: matlab -batch "cd matlab/tests; exit(run_tests() > 0)"
if nargin < 1, pattern = 'test_*.m'; end
here = fileparts(mfilename('fullpath'));
addpath(fileparts(here));
files = dir(fullfile(here, pattern));
failures = 0;
for i = 1:numel(files)
    name = files(i).name;
    started = tic;
    try
        run_isolated(fullfile(here, name));
        leaked = filly_mex('handleCount');
        assert(leaked == 0, 'filly:test', '%d gateway handles remain after the test', leaked);
        fprintf('PASS %s (%.1f s)\n', name, toc(started));
    catch err
        failures = failures + 1;
        fprintf('FAIL %s: %s\n', name, error_report(err));
    end
end
fprintf('%d of %d test files failed\n', failures, numel(files));
end

function run_isolated(path)
run(path);
end
