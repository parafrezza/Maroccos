################################################################################
# CONFIGURE PROJECT MAKEFILE (optional)
#   This file is where we make project specific configurations.
################################################################################

################################################################################
# OF ROOT
#   The location of your root openFrameworks installation
#       (default) OF_ROOT = ../../.. 
################################################################################
UNAME_S := $(shell uname -s 2>/dev/null)
UNAME_M := $(shell uname -m 2>/dev/null)

ifndef OF_ROOT
ifneq ($(findstring MINGW,$(UNAME_S)),)
OF_ROOT := ../OFF-ROOT/of_v0.12.1_msys2_mingw64_release
else ifneq ($(findstring MSYS,$(UNAME_S)),)
OF_ROOT := ../OFF-ROOT/of_v0.12.1_msys2_mingw64_release
else ifneq ($(findstring CYGWIN,$(UNAME_S)),)
OF_ROOT := ../OFF-ROOT/of_v0.12.1_msys2_mingw64_release
else ifneq ($(findstring Darwin,$(UNAME_S)),)
OF_ROOT := ../OFF-ROOT/of_v0.12.1_osx_release
else
OF_ROOT_BASE := ../OFF-ROOT/openFrameworks
OF_ROOT := $(OF_ROOT_BASE)
ifneq ($(wildcard $(OF_ROOT)/libs/openFrameworksCompiled/project/makefileCommon/compile.project.mk),)
# Lascia OF_ROOT_BASE così com'è (layout classico)
else
OF_ROOT_CANDIDATE := $(firstword $(wildcard $(OF_ROOT_BASE)/*/libs/openFrameworksCompiled/project/makefileCommon/compile.project.mk))
ifneq ($(OF_ROOT_CANDIDATE),)
OF_ROOT := $(patsubst %/libs/openFrameworksCompiled/project/makefileCommon/compile.project.mk,%,$(OF_ROOT_CANDIDATE))
endif
endif
endif
endif

# Tune platform variables when building on Raspberry Pi / ARM boards with the
# official linuxarmv* openFrameworks packages. These releases expect
# PLATFORM_OS to match the specific ARM flavour, otherwise config.shared.mk
# aborts thinking the wrong archive is in use.
ifeq ($(PLATFORM_OS),)
ifneq ($(filter armv6l,$(UNAME_M)),)
PLATFORM_OS=linuxarmv6l
else ifneq ($(filter armv7l,$(UNAME_M)),)
PLATFORM_OS=linuxarmv7l
else ifneq ($(filter aarch64,$(UNAME_M)),)
PLATFORM_OS=linuxaarch64
endif
endif


################################################################################
# PROJECT ROOT
#   The location of the project - a starting place for searching for files
#       (default) PROJECT_ROOT = . (this directory)
#    
################################################################################
PROJECT_ROOT := .

################################################################################
# PROJECT SPECIFIC CHECKS
#   This is a project defined section to create internal makefile flags to 
#   conditionally enable or disable the addition of various features within 
#   this makefile.  For instance, if you want to make changes based on whether
#   GTK is installed, one might test that here and create a variable to check. 
################################################################################
# None

################################################################################
# PROJECT EXTERNAL SOURCE PATHS
#   These are fully qualified paths that are not within the PROJECT_ROOT folder.
#   Like source folders in the PROJECT_ROOT, these paths are subject to 
#   exlclusion via the PROJECT_EXLCUSIONS list.
#
#     (default) PROJECT_EXTERNAL_SOURCE_PATHS = (blank) 
#
#   Note: Leave a leading space when adding list items with the += operator
################################################################################
# PROJECT_EXTERNAL_SOURCE_PATHS = 
#PROJECT_EXTERNAL_SOURCE_PATHS += $(OF_ROOT)/addons/ofxNetwork/src

# Disable OF default .ico because windres fails to preprocess it on this toolchain
override ICON :=
PROJECT_RELEASE_ICON =

################################################################################
# PROJECT EXCLUSIONS
#   These makefiles assume that all folders in your current project directory 
#   and any listed in the PROJECT_EXTERNAL_SOURCH_PATHS are are valid locations
#   to look for source code. The any folders or files that match any of the 
#   items in the PROJECT_EXCLUSIONS list below will be ignored.
#
#   Each item in the PROJECT_EXCLUSIONS list will be treated as a complete 
#   string unless teh user adds a wildcard (%) operator to match subdirectories.
#   GNU make only allows one wildcard for matching.  The second wildcard (%) is
#   treated literally.
#
#      (default) PROJECT_EXCLUSIONS = (blank)
#
#		Will automatically exclude the following:
#
#			$(PROJECT_ROOT)/bin%
#			$(PROJECT_ROOT)/obj%
#			$(PROJECT_ROOT)/%.xcodeproj
#
#   Note: Leave a leading space when adding list items with the += operator
################################################################################
# PROJECT_EXCLUSIONS =

################################################################################
# PROJECT LINKER FLAGS
#	These flags will be sent to the linker when compiling the executable.
#
#		(default) PROJECT_LDFLAGS = -Wl,-rpath=./libs
#
#   Note: Leave a leading space when adding list items with the += operator
################################################################################

# Currently, shared libraries that are needed are copied to the 
# $(PROJECT_ROOT)/bin/libs directory.  The following LDFLAGS tell the linker to
# add a runtime path to search for those shared libraries, since they aren't 
# incorporated directly into the final executable application binary.
# TODO: should this be a default setting?
# PROJECT_LDFLAGS=-Wl,-rpath=./libs

################################################################################
# PROJECT DEFINES
#   Create a space-delimited list of DEFINES. The list will be converted into 
#   CFLAGS with the "-D" flag later in the makefile.
#
#		(default) PROJECT_DEFINES = (blank)
#
#   Note: Leave a leading space when adding list items with the += operator
################################################################################
# PROJECT_DEFINES = 

################################################################################
# OPTIONAL: abilita finestra EGL/KMS (senza X) per Raspberry/Linux ARM
#   Usa: USE_EGL=1 make Release
################################################################################
ifeq ($(USE_EGL),1)
PROJECT_DEFINES += OF_USE_EGLWINDOW
endif
 
################################################################################
# WORKAROUND: POCO automatic network initializer
#   On MSYS2/MinGW, Poco in the official packages may be built without the
#   automatic network initializer symbol (pocoNetworkInitializer).
#   Defining POCO_NO_AUTOMATIC_LIB_INIT prevents referencing that symbol from
#   headers, avoiding undefined reference at link time. If needed, explicitly
#   call Poco::Net::initializeNetwork()/uninitializeNetwork() in your code
#   on Windows.
################################################################################
PROJECT_DEFINES += POCO_NO_AUTOMATIC_LIB_INIT

################################################################################
# PROJECT CFLAGS
#   This is a list of fully qualified CFLAGS required when compiling for this 
#   project.  These CFLAGS will be used IN ADDITION TO the PLATFORM_CFLAGS 
#   defined in your platform specific core configuration files. These flags are
#   presented to the compiler BEFORE the PROJECT_OPTIMIZATION_CFLAGS below. 
#
#		(default) PROJECT_CFLAGS = (blank)
#
#   Note: Before adding PROJECT_CFLAGS, note that the PLATFORM_CFLAGS defined in 
#   your platform specific configuration file will be applied by default and 
#   further flags here may not be needed.
#
#   Note: Leave a leading space when adding list items with the += operator
################################################################################
# PROJECT_CFLAGS = 

################################################################################
# PROJECT OPTIMIZATION CFLAGS
#   These are lists of CFLAGS that are target-specific.  While any flags could 
#   be conditionally added, they are usually limited to optimization flags. 
#   These flags are added BEFORE the PROJECT_CFLAGS.
#
#   PROJECT_OPTIMIZATION_CFLAGS_RELEASE flags are only applied to RELEASE targets.
#
#		(default) PROJECT_OPTIMIZATION_CFLAGS_RELEASE = (blank)
#
#   PROJECT_OPTIMIZATION_CFLAGS_DEBUG flags are only applied to DEBUG targets.
#
#		(default) PROJECT_OPTIMIZATION_CFLAGS_DEBUG = (blank)
#
#   Note: Before adding PROJECT_OPTIMIZATION_CFLAGS, please note that the 
#   PLATFORM_OPTIMIZATION_CFLAGS defined in your platform specific configuration 
#   file will be applied by default and further optimization flags here may not 
#   be needed.
#
#   Note: Leave a leading space when adding list items with the += operator
################################################################################
# PROJECT_OPTIMIZATION_CFLAGS_RELEASE = 
# PROJECT_OPTIMIZATION_CFLAGS_DEBUG = 

################################################################################
# PROJECT COMPILERS
#   Custom compilers can be set for CC and CXX
#		(default) PROJECT_CXX = (blank)
#		(default) PROJECT_CC = (blank)
#   Note: Leave a leading space when adding list items with the += operator
################################################################################
# PROJECT_CXX = 
# PROJECT_CC = 

# osx template

# Uncomment/comment below to switch between C++11 and C++17 ( or newer ). On macOS C++17 needs 10.15 or above.
# export MAC_OS_MIN_VERSION = 10.15
# export MAC_OS_CPP_VER = -std=c++17

################################################################################
# PROJECT LDFLAGS OVERRIDES FOR MSYS2
#   Explicitly add missing third-party libraries that MSYS2's makefiles do not
#   inject automatically when linking openFrameworks addons (Poco, tess2, kissfft).
################################################################################
ifneq ($(findstring MSYS,$(UNAME_S)),)
USER_LDFLAGS += -LC:/msys64/mingw64/lib
USER_LDFLAGS += -lPocoNetSSL -lPocoNet -lPocoCrypto -lPocoUtil -lPocoJSON -lPocoXML -lPocoFoundation
USER_LDFLAGS += -L$(OF_ROOT)/libs/kiss/lib/$(PLATFORM_LIB_SUBPATH)/$(PLATFORM_ARCH) -lkiss
USER_LDFLAGS += -L$(OF_ROOT)/libs/tess2/lib/$(PLATFORM_LIB_SUBPATH)/$(PLATFORM_ARCH) -ltess2
endif
