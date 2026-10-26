function tf = ptbAvailable()
% PTBAVAILABLE  Whether Psychtoolbox's Screen MEX file is on the path.
tf = exist('Screen', 'file') == 3;
end
