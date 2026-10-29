% Mesh assets: models share the vertex and index buffers and render like separate meshes.
r = filly.Renderer();
target = r.createRenderTarget(64, 64);
shape = filly.shapes.box('Width', 0.6, 'Height', 0.6, 'Depth', 0.6);
images = cell(1, 2);
for shared = [false true]
    scene = r.createScene();
    cam = r.createCamera();
    scene.camera = cam;
    cam.position = [0 0 6];
    cam.lookAt([0 0 0]);
    scene.addDirectionalLight([-1 -1 -2]);
    if shared
        asset = r.createMeshAsset(shape.positions, shape.indices, 'Normals', shape.normals, ...
            'BaseColor', [0.8 0.4 0.2 1]);
        empty = asset.memory;
        assert(empty.models == 0);
        models = scene.add(asset);
        for i = 2:4
            models(i) = scene.add(asset);
        end
        % Models add no geometry.
        assert(asset.memory.models == 4);
        assert(asset.memory.gpuGeometryBytes == empty.gpuGeometryBytes);
    else
        models = scene.createMesh(shape.positions, shape.indices, 'Normals', shape.normals, ...
            'BaseColor', [0.8 0.4 0.2 1]);
        for i = 2:4
            models(i) = scene.createMesh(shape.positions, shape.indices, 'Normals', shape.normals, ...
                'BaseColor', [0.8 0.4 0.2 1]);
        end
    end
    for i = 1:4
        models(i).position = [i - 2.5, 0, 0];
    end
    r.render(scene, target);
    images{shared + 1} = target.read();
    if shared
        % One model's material edit leaves the others as they were.
        before = images{2};
        models(1).material('mesh').baseColor = [0 0 1 1];
        assert(max(abs(models(2).material('mesh').baseColor - [0.8 0.4 0.2 1])) < 1e-6);
        r.render(scene, target);
        after = target.read();
        assert(~isequal(after(:, 1:20, :), before(:, 1:20, :)));
        assert(isequal(after(:, 30:end, :), before(:, 30:end, :)));
        % An update changes every model of the asset.
        models(2).updateMesh('Positions', shape.positions * 0.5);
        r.render(scene, target);
        smaller = target.read();
        assert(~isequal(smaller(:, 40:end, :), after(:, 40:end, :)));
        asset.close();
        expect_error(@() scene.add(asset), 'filly:error');
    end
    scene.close();
end
assert(isequal(images{1}, images{2}), 'shared and separate meshes must render the same image');
close(r);
