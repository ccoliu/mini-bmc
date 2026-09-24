# sensord — simulated BMC sensor daemon
#
#   make            build $(BUILD)/sensord
#   make test       build and run the Unity unit tests
#   make sanitize   unit tests + daemon under AddressSanitizer/UBSan (build/asan/)
#   make coverage   unit tests with gcov, lcov (>= 2.0) HTML report in build/cov/html/
#   make cppcheck   static analysis (fails on any finding)
#   make clean

CC      ?= cc
BUILD   ?= build
EXTRA_CFLAGS  ?=
EXTRA_LDFLAGS ?=

CFLAGS  = -std=c11 -D_POSIX_C_SOURCE=200809L -O2 -g \
          -Wall -Wextra -Werror -Wpedantic -Wshadow -Wstrict-prototypes \
          -Wmissing-prototypes -Wconversion -Wno-sign-conversion \
          $(EXTRA_CFLAGS)
LDFLAGS = $(EXTRA_LDFLAGS)
LDLIBS  = -lm

UNITY_DIR    = third_party/unity
UNITY_CFLAGS = -std=c11 -O1 -g -DUNITY_INCLUDE_DOUBLE $(EXTRA_CFLAGS)

LIB_SRCS = src/json.c src/sensors.c src/protocol.c
LIB_OBJS = $(LIB_SRCS:src/%.c=$(BUILD)/%.o)
DAEMON_OBJS = $(LIB_OBJS) $(BUILD)/server.o $(BUILD)/main.o

TEST_SRCS = $(wildcard tests/test_*.c)
TEST_BINS = $(TEST_SRCS:tests/%.c=$(BUILD)/%)

.PHONY: all test sanitize coverage cppcheck clean

all: $(BUILD)/sensord

$(BUILD)/sensord: $(DAEMON_OBJS)
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $^ $(LDLIBS)

$(BUILD)/%.o: src/%.c src/*.h | $(BUILD)
	$(CC) $(CFLAGS) -c -o $@ $<

$(BUILD)/unity.o: $(UNITY_DIR)/unity.c | $(BUILD)
	$(CC) $(UNITY_CFLAGS) -c -o $@ $<

$(BUILD)/test_%: tests/test_%.c $(LIB_OBJS) $(BUILD)/unity.o
	$(CC) $(CFLAGS) -DUNITY_INCLUDE_DOUBLE -Isrc -I$(UNITY_DIR) $(LDFLAGS) \
	    -o $@ $^ $(LDLIBS)

$(BUILD):
	mkdir -p $@

test: $(TEST_BINS)
	@set -e; for t in $(TEST_BINS); do echo "== $$t"; ./$$t; done

SAN_FLAGS = -O1 -fsanitize=address,undefined -fno-omit-frame-pointer \
            -fno-sanitize-recover=all

sanitize:
	$(MAKE) BUILD=build/asan EXTRA_CFLAGS="$(SAN_FLAGS)" \
	    EXTRA_LDFLAGS="-fsanitize=address,undefined" all test

coverage:
	$(MAKE) BUILD=build/cov EXTRA_CFLAGS="-O0 --coverage" EXTRA_LDFLAGS="--coverage" test
	lcov --capture --directory build/cov --output-file build/cov/coverage.info \
	    --rc branch_coverage=1
	lcov --extract build/cov/coverage.info '*/src/*' --output-file build/cov/coverage.info \
	    --rc branch_coverage=1
	genhtml build/cov/coverage.info --output-directory build/cov/html --branch-coverage
	lcov --summary build/cov/coverage.info --rc branch_coverage=1

cppcheck:
	cppcheck --std=c11 --enable=warning,style,performance,portability \
	    --error-exitcode=1 --inline-suppr --quiet \
	    --suppress=missingIncludeSystem -D_POSIX_C_SOURCE=200809L -Isrc src

clean:
	rm -rf build
