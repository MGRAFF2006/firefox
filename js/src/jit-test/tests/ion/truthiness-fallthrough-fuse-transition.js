// |jit-test| --fast-warmup; --no-threads
/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
// Each jit-test runs in a fresh shell process; a realm cannot reset this fuse.
// Exercise branch targets before/after invalidation, or with the fuse popped
// before warming. Generated-code layout is checked separately.

function checkFuse(expected) {
    if (getBuildConfiguration("debug") || getBuildConfiguration("fuzzing-defined")) {
        assertEq(getFuseState().HasSeenObjectEmulateUndefinedFuse.intact, expected);
    }
}
assertEq(typeof createIsHTMLDDA, "function");
checkFuse(true);

const alreadyPopped = false;
const earlyHTMLDDA = alreadyPopped ? createIsHTMLDDA() : null;
checkFuse(!alreadyPopped);

function truthyArm(value) {
    if (value) {
        return 17;
    }
    return 29;
}

function falsyArm(value) {
    if (!value) {
        return 29;
    }
    return 17;
}

function equalNull(value) {
    if (value == null) {
        return 5;
    }
    return 9;
}

function notEqualNull(value) {
    if (value != null) {
        return 9;
    }
    return 5;
}

function equalUndefined(value) {
    if (value == undefined) {
        return 5;
    }
    return 9;
}

function notEqualUndefined(value) {
    if (value != undefined) {
        return 9;
    }
    return 5;
}

// These functions see only objects, keeping the object-specialized sites available.
function objectTruthyArm(value) {
    if (value) {
        return 17;
    }
    return 29;
}

function objectFalsyArm(value) {
    if (!value) {
        return 29;
    }
    return 17;
}

function objectEqualNull(value) {
    if (value == null) {
        return 5;
    }
    return 9;
}

function objectNotEqualNull(value) {
    if (value != null) {
        return 9;
    }
    return 5;
}

function objectEqualUndefined(value) {
    if (value == undefined) {
        return 5;
    }
    return 9;
}

function objectNotEqualUndefined(value) {
    if (value != undefined) {
        return 9;
    }
    return 5;
}

const cases = [
    [false, 29, 9], [true, 17, 9], [undefined, 29, 5], [null, 29, 5],
    [NaN, 29, 9], [-0, 29, 9], [0, 29, 9], [1, 17, 9],
    ["", 29, 9], ["x", 17, 9], [0n, 29, 9], [1n, 17, 9],
    [Symbol(), 17, 9], [{}, 17, 9], [new Proxy({}, {}), 17, 9],
];

function check(value, branch, equality) {
    assertEq(truthyArm(value), branch);
    assertEq(falsyArm(value), branch);
    assertEq(equalNull(value), equality);
    assertEq(notEqualNull(value), equality);
    assertEq(equalUndefined(value), equality);
    assertEq(notEqualUndefined(value), equality);
}

function checkObject(value, branch, equality) {
    assertEq(objectTruthyArm(value), branch);
    assertEq(objectFalsyArm(value), branch);
    assertEq(objectEqualNull(value), equality);
    assertEq(objectNotEqualNull(value), equality);
    assertEq(objectEqualUndefined(value), equality);
    assertEq(objectNotEqualUndefined(value), equality);
}

const ordinaryObjects = [{}, new Proxy({}, {})];

// First iteration is cold; subsequent iterations exercise warmed execution.
for (let round = 0; round < 2000; round++) {
    for (const [value, branch, equality] of cases) {
        check(value, branch, equality);
    }
    for (const object of ordinaryObjects) {
        checkObject(object, 17, 9);
    }
    if (alreadyPopped) {
        check(earlyHTMLDDA, 29, 5);
        checkObject(earlyHTMLDDA, 29, 5);
    }
}

checkFuse(!alreadyPopped);
const htmlDDA = alreadyPopped ? earlyHTMLDDA : createIsHTMLDDA();
checkFuse(false);

for (let round = 0; round < 2000; round++) {
    check(htmlDDA, 29, 5);
    checkObject(htmlDDA, 29, 5);
    for (const [value, branch, equality] of cases) {
        check(value, branch, equality);
    }
    for (const object of ordinaryObjects) {
        checkObject(object, 17, 9);
    }
}

