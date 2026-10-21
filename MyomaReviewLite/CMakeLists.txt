set(MODULE_NAME MyomaReviewLite)

file(GLOB MODULE_LIBRARY_SCRIPTS
  RELATIVE "${CMAKE_CURRENT_SOURCE_DIR}"
  CONFIGURE_DEPENDS
  "${CMAKE_CURRENT_SOURCE_DIR}/MyomaReviewLiteLib/*.py")

set(MODULE_PYTHON_SCRIPTS
  ${MODULE_NAME}.py
  ${MODULE_LIBRARY_SCRIPTS}
  )

set(MODULE_PYTHON_RESOURCES
  Resources/Icons/${MODULE_NAME}.svg
  Resources/Icons/chevron-left.svg
  Resources/Icons/chevron-right.svg
  Resources/Icons/chevron-down.svg
  Resources/Icons/comment.svg
  Resources/Icons/comment-written.svg
  Resources/Icons/tool-paint.svg
  Resources/Icons/tool-erase.svg
  Resources/Icons/tool-draw.svg
  Resources/Icons/tool-scissors.svg
  Resources/Icons/tool-islands.svg
  Resources/Icons/tool-smooth.svg
  Resources/Fonts/HankenGrotesk-Regular.ttf
  Resources/Fonts/HankenGrotesk-Medium.ttf
  Resources/Fonts/HankenGrotesk-SemiBold.ttf
  Resources/Fonts/HankenGrotesk-Bold.ttf
  Resources/Fonts/OFL.txt
  )

slicerMacroBuildScriptedModule(
  NAME ${MODULE_NAME}
  SCRIPTS ${MODULE_PYTHON_SCRIPTS}
  RESOURCES ${MODULE_PYTHON_RESOURCES}
  )
