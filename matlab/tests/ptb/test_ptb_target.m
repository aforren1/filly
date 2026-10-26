% filly.PTBTarget in the window configurations of phase 0: pixels, orientation, synchronization
% under slow frames, stereo, and three close orders, including close after `sca`.
configs = {'plain', 'imaging', 'msaa4', 'float32', 'stereo4'};
orders = {'renderer', 'sca', 'target', 'renderer', 'sca'};
for c = 1:numel(configs)
    [win, stereo] = ptb_open(configs{c});
    try
        r = filly.Renderer.forWindow(win);
        assert(r.sharedContext == filly.currentContext());
        [w, h] = Screen('WindowSize', win);
        tgt = filly.PTBTarget(r, win);
        assert(tgt.width == w && tgt.height == h);
        scene = quad_scene(r, w, h);

        % The window shows exactly what an offscreen render reads back, upright.
        offscreen = r.createRenderTarget(w, h);
        r.render(scene, offscreen);
        expected = offscreen.read();
        r.render(scene, tgt);
        if stereo
            tgt.acquire();
            for eye = 0:1
                Screen('SelectStereoDrawBuffer', win, eye);
                tgt.draw();
            end
            tgt.release();
            Screen('SelectStereoDrawBuffer', win, 0);
        else
            tgt.draw();
        end
        assert(~tgt.acquired);
        shown = Screen('GetImage', win, [0 0 w h], 'drawBuffer');
        assert(isequal(shown, expected(:, :, 1:3)), '%s: the window must match the offscreen image', configs{c});
        Screen('Flip', win);

        % A slow frame: the draw waits for Filament on the GPU, so each frame shows its own color.
        heavy = r.createScene();
        heavy.camera = scene.camera;
        heavy.msaa = 4;
        heavy.ssao = true;
        sphere = filly.shapes.uvSphere('Radius', 0.5, 'Segments', 384, 'Rings', 192);
        for i = 1:12
            model = heavy.createMesh(sphere.positions, sphere.indices, 'Normals', sphere.normals, 'Unlit', true);
            meshNodes = model.nodes;
            if i == 1, materials = meshNodes(1).material(); else, materials(i) = meshNodes(1).material(); end
        end
        colors = [1 0 0 1; 0 1 0 1];
        for frame = 1:12
            k = mod(frame, 2) + 1;
            setParameters(materials, 'baseColor', repmat(colors(k, :), numel(materials), 1));
            r.render(heavy, tgt);
            tgt.draw();
            shown = Screen('GetImage', win, [0 0 w h], 'drawBuffer');
            Screen('Flip', win);
            center = double(squeeze(shown(round(h / 2), round(w / 2), :)))';
            assert(max(abs(center - 255 * colors(k, 1:3))) <= 2, '%s: frame %d shows a stale or partial render', ...
                configs{c}, frame);
        end

        % Close orders. PTB owns the texture; filly releases it after Filament no longer uses it.
        texture = tgt.texture;
        switch orders{c}
            case 'renderer'
                close(r);
                assert(tgt.closed && isempty(r.HostTextures));
                assert(~any(Screen('Windows') == texture), 'the PTB texture must be closed with the renderer');
                sca;
            case 'sca'
                sca;
                close(r);
                assert(tgt.closed && isempty(r.HostTextures));
            case 'target'
                close(tgt);
                assert(tgt.closed && isempty(r.HostTextures));
                assert(~any(Screen('Windows') == texture), 'the PTB texture must be closed with the target');
                close(r);
                sca;
        end
        clear r tgt scene offscreen heavy materials model meshNodes
    catch err
        sca;
        rethrow(err);
    end
end
