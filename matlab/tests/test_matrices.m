% 4 x 4 matrices: MATLAB's column-major arrays with column vectors, the same mathematics as the
% Python API's row-major arrays.
r = filly.Renderer();
scene = r.createScene();
model = scene.createMesh([0 0 0; 1 0 0; 0 1 0], [1 2 3]);

angle = pi / 6;
rotation = [cos(angle) -sin(angle) 0 0; sin(angle) cos(angle) 0 0; 0 0 1 0; 0 0 0 1];
translation = [1 0 0 1; 0 1 0 2; 0 0 1 3; 0 0 0 1];
T = translation * rotation;
model.transform = T;
assert(max(abs(model.transform(:) - T(:))) < 1e-6);
% The translation is the fourth column, as for column vectors.
assert(max(abs(model.position - [1 2 3])) < 1e-6);
assert(max(abs(model.rotationEulerDeg - [0 0 30])) < 1e-4);

% single input is accepted as well.
model.transform = single(translation);
assert(max(abs(model.transform(:) - translation(:))) < 1e-6);

% The view matrix is the inverse of the camera's pose.
cam = r.createCamera();
cam.position = [0 1 5];
cam.lookAt([0 0 0]);
assert(max(max(abs(cam.viewMatrix * cam.transform - eye(4)))) < 1e-5);

% Non-affine and non-finite matrices are rejected.
bad = T;
bad(4, 1) = 1;
expect_error(@() filly_mex('set', model.Id, 'transform', bad), 'filly:invalidArgument');
bad = T;
bad(1, 1) = NaN;
expect_error(@() filly_mex('set', model.Id, 'transform', bad), 'filly:invalidArgument');
expect_error(@() filly_mex('set', model.Id, 'transform', eye(3)), 'filly:invalidArgument');
