# Firmware user module. Pass this directory as USER_C_MODULES:
#   make USER_C_MODULES=/path/to/atom-s3r-ota/native
# That is a firmware image, not a file copy onto the current UiFlow2 build.

add_library(usermod_gh_render INTERFACE)
target_sources(usermod_gh_render INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/gh_usermod.c
    ${CMAKE_CURRENT_LIST_DIR}/gh_blit.c
)
target_include_directories(usermod_gh_render INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
)
target_link_libraries(usermod INTERFACE usermod_gh_render)
