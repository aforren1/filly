function context = currentContext()
% CURRENTCONTEXT  The OpenGL context current on this thread (HGLRC or GLXContext), or 0.
context = filly_mex('currentContext');
end
