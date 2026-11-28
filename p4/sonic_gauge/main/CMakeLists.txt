idf_component_register(
    SRCS "main.c" "pix6.c" "scene.c" "radio.c"
         "${CMAKE_SOURCE_DIR}/../../native/gh_blit.c"
    INCLUDE_DIRS "." "${CMAKE_SOURCE_DIR}/../../native"
    REQUIRES esp_wifi nvs_flash esp_event esp_netif esp_timer esp_lcd espressif__esp_hosted
)
target_add_binary_data(${COMPONENT_LIB} "${CMAKE_SOURCE_DIR}/../../pixidx.bin" BINARY)
