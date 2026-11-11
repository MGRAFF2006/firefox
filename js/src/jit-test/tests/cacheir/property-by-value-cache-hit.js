setJitCompilerOption("ic.force-megamorphic", 1);

function get(obj, key) {
  return obj[key];
}

function hasOwn(obj, key) {
  return Object.hasOwn(obj, key);
}

function check(obj, key, expected) {
  for (let i = 0; i < 100; i++) {
    assertEq(get(obj, key), expected);
  }
}

function testSlots() {
  const fixed = {true: 1, false: 2};
  const dynamic = {};
  for (let i = 0; i < 24; i++) {
    dynamic["padding" + i] = i;
  }
  dynamic.true = 3;
  dynamic.false = 4;

  for (const obj of [fixed, dynamic]) {
    for (const key of [true, false]) {
      const initial = key ? (obj === fixed ? 1 : 3) : (obj === fixed ? 2 : 4);
      check(obj, key, initial);
      obj[key] = initial + 10;
      check(obj, key, initial + 10);
      gc();
      check(obj, key, initial + 10);
    }
  }
}
testSlots();

function testPrototypeChanges() {
  const holder = {true: 11};
  const middle = Object.create(holder);
  const obj = Object.create(middle);
  for (let i = 0; i < 100; i++) {
    assertEq(hasOwn(obj, true), false);
  }
  check(obj, true, 11);
  for (let i = 0; i < 100; i++) {
    assertEq(hasOwn(obj, true), false);
    assertEq(get(obj, true), 11);
  }
  holder.true = 12;
  check(obj, true, 12);

  Object.setPrototypeOf(middle, {true: 21});
  check(obj, true, 21);
  obj.true = 31;
  check(obj, true, 31);
  delete obj.true;
  check(obj, true, 21);

  delete Object.getPrototypeOf(middle).true;
  check(obj, true, undefined);
  Object.getPrototypeOf(middle).true = 41;
  check(obj, true, 41);

  let calls = 0;
  Object.defineProperty(middle, "true", {
    configurable: true,
    get() {
      calls++;
      return 51;
    },
  });
  check(obj, true, 51);
  assertEq(calls, 100);
  delete middle.true;
  check(obj, true, 41);
}
testPrototypeChanges();

function testFallbacks() {
  let calls = 0;
  const obj = new Proxy({}, {
    get(target, key) {
      assertEq(key, "true");
      calls++;
      return 61;
    },
  });
  check(obj, true, 61);
  assertEq(calls, 100);

  const key = Symbol("key");
  const native = {true: 71, [key]: 72, 0: 73};
  check(native, "true", 71);
  check(native, key, 72);
  check(native, "0", 73);
}
testFallbacks();
