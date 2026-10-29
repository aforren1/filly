% The gateway without a renderer: command dispatch, handle checks, argument conversion, and error
% identifiers. Hosted CI runners have no usable OpenGL device, so CI runs only this file.
assert(filly_mex('handleCount') == 0, 'a fresh session must have no handles');

% The MEX file carries the version from the git tags. On a tag push, CI sets FILLY_RELEASE_TAG,
% so that a package built from wrong or missing git history cannot be released.
tag = getenv('FILLY_RELEASE_TAG');
if isempty(tag)
    assert(~isempty(regexp(filly.version(), '^\d+\.\d+\.\d+(\.dev\d+)?$', 'once')), ...
        'filly.version() is %s, not X.Y.Z or X.Y.Z.devN', filly.version());
else
    assert(strcmp(['v' filly.version()], tag), 'filly.version() is %s, the release tag is %s', filly.version(), tag);
end

% Dispatch and handles.
expect_error(@() filly_mex(), 'filly:invalidArgument');
expect_error(@() filly_mex('noSuchCommand'), 'filly:invalidArgument');
expect_error(@() filly_mex(42), 'filly:invalidArgument');
expect_error(@() filly_mex('get', uint64(123456789), 'closed'), 'filly:error');
expect_error(@() filly_mex('get', 1, 'closed'), 'filly:invalidArgument');
expect_error(@() filly_mex('get', uint64([1 2]), 'closed'), 'filly:invalidArgument');
% Release is idempotent: MATLAB can delete an object after the exit handler emptied the table.
filly_mex('release', uint64(123456789));
assert(~filly_mex('valid', uint64(123456789)), 'an unknown handle must not be valid');

% A renderer argument error raises before any engine starts.
expect_error(@() filly.Renderer('SharedContext', 0), 'filly:interop');
expect_error(@() filly.Renderer('NoSuchOption', 1), 'filly:invalidArgument');

% Batch calls check their handles before any update; an empty batch needs no renderer.
nothing = zeros(1, 0, 'uint64');
filly_mex('Node.setTransforms', nothing, zeros(4, 4, 0));
filly_mex('Material.setParameters', nothing, 'roughness', zeros(0, 1));
expect_error(@() filly_mex('Node.setTransforms', uint64(123456789), eye(4)), 'filly:error');
expect_error(@() filly_mex('Node.setTransforms', 1, eye(4)), 'filly:invalidArgument');
expect_error(@() filly_mex('Node.setTransforms', nothing, eye(4)), 'filly:invalidArgument');
expect_error(@() filly_mex('Node.setTransforms', nothing, int32(zeros(4, 4, 0))), 'filly:invalidArgument');
expect_error(@() filly_mex('Material.setParameters', nothing, 'shininess', zeros(0, 1)), 'filly:invalidArgument');

% Procedural shapes are native and need no renderer. Indices are 1-based.
box = filly.shapes.box('Width', 2);
assert(size(box.positions, 2) == 3 && size(box.normals, 2) == 3 && size(box.uvs, 2) == 2);
assert(size(box.positions, 1) == 24 && size(box.indices, 2) == 3 && size(box.indices, 1) == 12);
assert(min(box.indices(:)) == 1 && max(box.indices(:)) == 24);
assert(max(box.positions(:, 1)) == 1 && min(box.positions(:, 1)) == -1);
sphere = filly.shapes.uvSphere('Radius', 0.5);
assert(all(abs(vecnorm(sphere.positions, 2, 2) - 0.5) < 1e-6));
expect_error(@() filly.shapes.box('Width', -1), 'filly:invalidArgument');

assert(filly_mex('handleCount') == 0, 'the checks must leave no handles');
