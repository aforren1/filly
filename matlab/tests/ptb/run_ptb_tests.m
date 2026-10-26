function failures = run_ptb_tests()
% RUN_PTB_TESTS  Psychtoolbox tests. They need a display and open windows on screen 0.
%   The test_ptb_*.m scripts run in this process. The scenario_*.m scripts end the MATLAB
%   session in a particular state (after `clear all`, at exit with a window open), so each
%   runs in its own MATLAB or octave-cli process and must print SCENARIO OK and exit with
%   status 0.
%
%   From a shell: matlab -batch "cd matlab/tests/ptb; exit(run_ptb_tests() > 0)"
%   or: octave-cli --eval "cd matlab/tests/ptb; exit(run_ptb_tests() > 0)"
here = fileparts(mfilename('fullpath'));
addpath(fileparts(fileparts(here)));
addpath(fileparts(here));
failures = 0;
tests = dir(fullfile(here, 'test_ptb_*.m'));
for i = 1:numel(tests)
    started = tic;
    try
        run_isolated(fullfile(here, tests(i).name));
        fprintf('PASS %s (%.1f s)\n', tests(i).name, toc(started));
    catch err
        failures = failures + 1;
        fprintf('FAIL %s: %s\n', tests(i).name, error_report(err));
    end
end
scenarios = dir(fullfile(here, 'scenario_*.m'));
if exist('OCTAVE_VERSION', 'builtin')
    % octave-cli exits with status 1 when the --eval code raises an error, as -batch does.
    launch = sprintf('"%s" --no-gui --no-init-file --quiet --eval', fullfile(OCTAVE_HOME, 'bin', 'octave-cli'));
else
    launch = sprintf('"%s" -batch', fullfile(matlabroot, 'bin', 'matlab'));
end
for i = 1:numel(scenarios)
    [~, name] = fileparts(scenarios(i).name);
    command = sprintf('%s "cd(''%s''); %s"', launch, here, name);
    started = tic;
    [status, output] = system(command);
    if status == 0 && ~isempty(strfind(output, 'SCENARIO OK'))
        fprintf('PASS %s (%.1f s, separate process)\n', name, toc(started));
    else
        failures = failures + 1;
        fprintf('FAIL %s: exit status %d\n%s\n', name, status, output);
    end
end
fprintf('%d PTB tests failed\n', failures);
end

function run_isolated(path)
run(path);
end
