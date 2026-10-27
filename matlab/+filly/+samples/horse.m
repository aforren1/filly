function path = horse()
% HORSE  Path of the bundled sample model "Wooden Playground Horse" by Batuhan13.
%   A textured glTF binary with one animation clip, 2.2 MB. It is licensed CC BY 4.0: give
%   attribution with images rendered from it, as samples/ATTRIBUTION.md in the package states.
%
%   scene.load(filly.samples.horse())
path = fullfile(fileparts(fileparts(fileparts(mfilename('fullpath')))), 'samples', ...
    'wooden_playground_horse.glb');
end
