% filly.PTBTexture: PTB draws into a texture that a filly material samples.
win = ptb_open('offscreen');
try
    r = filly.Renderer.forWindow(win);
    w = 64;
    h = 32;
    tex = filly.PTBTexture(r, win, w, h, 'Filter', 'nearest');
    assert(tex.width == w && tex.height == h && strcmp(tex.colorSpace, 'srgb'));

    % Quadrants in PTB's coordinates, whose origin is the top left.
    tex.beginWrite();
    assert(tex.writing);
    Screen('FillRect', tex.texture, [255 0 0], [0 0 w / 2 h / 2]);
    Screen('FillRect', tex.texture, [0 255 0], [w / 2 0 w h / 2]);
    Screen('FillRect', tex.texture, [0 0 255], [0 h / 2 w / 2 h]);
    Screen('FillRect', tex.texture, [255 255 255], [w / 2 h / 2 w h]);
    tex.endWrite();
    assert(~tex.writing);

    % A quad that fills the target, with glTF UVs: (0, 0) is the top-left corner of the image.
    scene = r.createScene();
    cam = r.createCamera();
    scene.camera = cam;
    aspect = w / h;
    cam.setOrthographic('Left', -aspect, 'Right', aspect, 'Bottom', -1, 'Top', 1, 'Near', 0.1, 'Far', 10);
    cam.position = [0 0 5];
    cam.lookAt([0 0 0]);
    quad = scene.createMesh([-aspect -1 0; aspect -1 0; aspect 1 0; -aspect 1 0], [1 2 3; 1 3 4], ...
        'Uvs', [0 1; 1 1; 1 0; 0 0], 'Unlit', true);
    meshNodes = quad.nodes;
    material = meshNodes(1).material();
    material.baseColorTexture = tex;
    target = r.createRenderTarget(w, h);
    r.render(scene, target);
    img = target.read();
    corner = @(x, y) double(squeeze(img(y, x, 1:3)))';
    shown = {corner(8, 8), corner(w - 8, 8), corner(8, h - 8), corner(w - 8, h - 8)};
    fprintf('  PTBTexture corners as sampled (tl, tr, bl, br): %s %s %s %s\n', mat2str(shown{1}), ...
        mat2str(shown{2}), mat2str(shown{3}), mat2str(shown{4}));
    % PTB draws into the texture with OpenGL's bottom-up rows, so with glTF UVs the image is
    % upside down; a texture transform of the slot flips it back.
    material.baseColorTextureScale = [1 -1];
    material.baseColorTextureOffset = [0 1];
    r.render(scene, target);
    img = target.read();
    corner = @(x, y) double(squeeze(img(y, x, 1:3)))';
    assert(isequal(corner(8, 8), [255 0 0]) && isequal(corner(w - 8, 8), [0 255 0]) && ...
        isequal(corner(8, h - 8), [0 0 255]) && isequal(corner(w - 8, h - 8), [255 255 255]), ...
        'with the flip, the texture must show what PTB drew, upright');

    % A second write waits for the frame that sampled the first.
    tex.beginWrite();
    Screen('FillRect', tex.texture, [0 0 0]);
    tex.endWrite();
    r.render(scene, target);
    img = target.read();
    assert(all(all(img(:, :, 1) == 0)));

    texture = tex.texture;
    close(tex);
    assert(tex.closed && isempty(r.HostTextures));
    assert(~any(Screen('Windows') == texture));
    close(r);
    sca;
catch err
    sca;
    rethrow(err);
end
