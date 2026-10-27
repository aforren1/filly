function path = horse()
% HORSE  Path of the bundled sample model "Horse" by Quaternius.
%   One vertex-colored mesh with a 1.5-second gallop clip that animates morph targets, 178 KB.
%   It is in the public domain (CC0 1.0); samples/ATTRIBUTION.md in the package gives its source.
%
%   horse = scene.load(filly.samples.horse());
%   horse.applyAnimation(1, t);   % the gallop at time t (seconds), looped
path = fullfile(fileparts(fileparts(fileparts(mfilename('fullpath')))), 'samples', 'horse.glb');
end
