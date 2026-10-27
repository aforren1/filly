% Error identifiers, closed and stale handles, delete order, and the MEX lock.
r = filly.Renderer();
assert(mislocked('filly_mex'), 'the gateway must lock itself once a renderer exists');
scene = r.createScene();

% Core error classes map to identifiers.
expect_error(@() filly_mex('set', scene.Id, 'encoding', 'bogus'), 'filly:invalidArgument');
expect_error(@() scene.load(fullfile(tempdir, 'filly-missing-file.glb')), 'filly:asset');
expect_error(@() filly_mex('no such command'), 'filly:invalidArgument');
expect_error(@() filly_mex('get', scene.Id, 'noSuchProperty'), 'filly:invalidArgument');
expect_error(@() r.render(scene, r.createRenderTarget(4, 4)), 'filly:error');  % no camera
expect_error(@() r.createRenderTarget(0, 4), 'filly:invalidArgument');
expect_error(@() filly.Renderer('SharedContext', uint64(0)), 'filly:interop');
% Wrong handle types and wrong classes are argument errors.
expect_error(@() filly_mex('Scene.close', r.Id), 'filly:invalidArgument');
expect_error(@() filly_mex('Scene.close', 1), 'filly:invalidArgument');

% A closed object raises filly:error; repeated closes do nothing.
closedScene = r.createScene();
closedScene.close();
closedScene.close();
assert(closedScene.closed);
expect_error(@() closedScene.background, 'filly:error');

% A handle whose MATLAB object was deleted is rejected, not reused.
mesh = scene.createMesh([0 0 0; 1 0 0; 0 1 0], [1 2 3]);
id = mesh.Id;
delete(mesh);
expect_error(@() filly_mex('get', id, 'visible'), 'filly:error');
% Releasing twice is harmless.
filly_mex('release', id);

% Deleting a MATLAB handle does not close the native object: the scene keeps its model.
mesh = scene.createMesh([0 0 0; 1 0 0; 0 1 0], [1 2 3]);
nodes = mesh.nodes;
clear mesh
stats = r.stats;
assert(stats.liveModels == 2);

% Objects keep their renderer open: clearing the variable does not close it.
r2 = filly.Renderer();
scene2 = r2.createScene();
clear r2
assert(~scene2.closed);
scene2.background = [0 0 0 1];
% delete() on the renderer closes it and everything that it owns.
owner = scene2.Owner;
delete(owner);
assert(scene2.closed);
expect_error(@() scene2.background, 'filly:error');

% Asset compatibility messages become warnings with an identifier (none for this file).
% The path first: Octave 11 warns about its own mix of string types in fullfile.
horsePath = filly.samples.horse();
lastwarn('');
horse = scene.load(horsePath);
[~, warningId] = lastwarn();
assert(isempty(warningId) || strcmp(warningId, 'filly:assetCompatibility'));

% GLB bytes load as well as paths.
fid = fopen(horsePath, 'r');
bytes = fread(fid, Inf, '*uint8');
fclose(fid);
fromBytes = scene.load(bytes);
assert(isequal(fromBytes.nodeNames, horse.nodeNames));

close(r);
expect_error(@() r.createScene(), 'filly:error');

