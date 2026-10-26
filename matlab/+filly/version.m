function v = version()
% VERSION  The version of the filly MATLAB and Octave frontend, from its MEX file.
%   The frontend has its own version, apart from the Python package.
v = filly_mex('version');
end
