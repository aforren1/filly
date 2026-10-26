function SetupFilly(save)
% SETUPFILLY  Add the filly MATLAB frontend to the path and check that its MEX file loads.
%   SetupFilly() adds this folder for the current session. SetupFilly(true) also saves the
%   path. See docs/how-to/matlab.md.
here = fileparts(mfilename('fullpath'));
addpath(here);
if exist('filly_mex', 'file') ~= 3
    error('filly:setup', ['No filly_mex MEX file in %s. Build it with CMake ' ...
        '(-DFILLY_MATLAB=ON); see docs/how-to/matlab.md.'], here);
end
count = filly_mex('handleCount');
fprintf('filly: %s loaded (%d open handles)\n', which('filly_mex'), count);
if nargin > 0 && save
    savepath;
end
end
