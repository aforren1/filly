function v = version()
% VERSION  The version of filly, from its MEX file.
%   The same version as the Python package, from the vX.Y.Z git tags.
v = filly_mex('version');
end
