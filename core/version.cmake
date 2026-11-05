# FILLY_VERSION for the frontends that do not get it from setuptools-scm (MATLAB, Octave, web).
#
# It is the version of the vX.Y.Z tags, the string that setuptools-scm computes for the Python
# package (pyproject.toml): X.Y.Z on a clean tagged commit, else X.Y.(Z+1).devN, N commits after
# the tag. It is computed at configure time, so a new commit shows after the next configure. A
# source tree without git history needs FILLY_VERSION, or reports 0.0.0.
set(FILLY_VERSION "" CACHE STRING "Version to report instead of the one from the git tags")
if(NOT FILLY_VERSION)
  set(FILLY_VERSION 0.0.0)
  find_package(Git QUIET)
  if(Git_FOUND)
    execute_process(COMMAND "${GIT_EXECUTABLE}" describe --dirty --tags --long --match "v[0-9]*"
                    WORKING_DIRECTORY "${PROJECT_SOURCE_DIR}" OUTPUT_VARIABLE _describe
                    RESULT_VARIABLE _result OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
    if(_result EQUAL 0 AND _describe MATCHES "^v([0-9]+)\\.([0-9]+)\\.([0-9]+)-([0-9]+)-g[0-9a-f]+(-dirty)?$")
      if(CMAKE_MATCH_4 EQUAL 0 AND NOT CMAKE_MATCH_5)
        set(FILLY_VERSION "${CMAKE_MATCH_1}.${CMAKE_MATCH_2}.${CMAKE_MATCH_3}")
      else()
        math(EXPR _patch "${CMAKE_MATCH_3} + 1")
        set(FILLY_VERSION "${CMAKE_MATCH_1}.${CMAKE_MATCH_2}.${_patch}.dev${CMAKE_MATCH_4}")
      endif()
    else()
      # No tag in the history, for example in a shallow clone: setuptools-scm counts from 0.0.
      execute_process(COMMAND "${GIT_EXECUTABLE}" rev-list --count HEAD
                      WORKING_DIRECTORY "${PROJECT_SOURCE_DIR}" OUTPUT_VARIABLE _count
                      RESULT_VARIABLE _result OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
      if(_result EQUAL 0)
        set(FILLY_VERSION "0.0.1.dev${_count}")
      endif()
    endif()
  endif()
endif()
message(STATUS "filly version ${FILLY_VERSION}")
