function scene = quad_scene(r, w, h)
% QUAD_SCENE  Unlit quads: red top left, green top right, blue bottom left, on grey (188 in sRGB).
scene = r.createScene();
cam = r.createCamera();
scene.camera = cam;
scene.background = [0.5 0.5 0.5 1];
aspect = w / h;
cam.setOrthographic('Left', -aspect, 'Right', aspect, 'Bottom', -1, 'Top', 1, 'Near', 0.1, 'Far', 10);
cam.position = [0 0 5];
cam.lookAt([0 0 0]);
quad = @(x0, y0, x1, y1) [x0 y0 0; x1 y0 0; x1 y1 0; x0 y1 0];
faces = [1 2 3; 1 3 4];
scene.createMesh(quad(-aspect + 0.1, 0.2, -0.3, 0.9), faces, 'BaseColor', [1 0 0 1], 'Unlit', true);
scene.createMesh(quad(0.3, 0.2, aspect - 0.1, 0.9), faces, 'BaseColor', [0 1 0 1], 'Unlit', true);
scene.createMesh(quad(-aspect + 0.1, -0.9, -0.3, -0.2), faces, 'BaseColor', [0 0 1 1], 'Unlit', true);
end
