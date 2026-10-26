function expect_error(f, identifier)
% EXPECT_ERROR  Assert that calling f raises an error with the given identifier.
actual = '';
try
    f();
catch err
    actual = err.identifier;
end
assert(strcmp(actual, identifier), 'filly:test', 'expected error %s, got ''%s''', identifier, actual);
end
