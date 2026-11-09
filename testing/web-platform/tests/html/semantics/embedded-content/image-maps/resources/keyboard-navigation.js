let nextMapId = 0;

function mapFixture(t, {empty = false, mapped = true} = {}) {
  const name = `keyboard-map-${nextMapId++}`;
  const container = document.createElement("div");
  container.innerHTML = `
    <button>Start</button>
    <img alt="Map destinations" width="300" height="200">
    <map name="${name}">
      <area shape="rect" coords="0,0,100,200" href="#one" alt="One">
      <area shape="rect" coords="100,0,200,200" href="#two" alt="Two">
    </map>
    <button>End</button>`;
  const [start, end] = container.querySelectorAll("button");
  const image = container.querySelector("img");
  const map = container.querySelector("map");
  if (mapped) {
    image.useMap = `#${name}`;
  }
  if (empty) {
    map.replaceChildren();
  }
  document.body.append(container);
  t.add_cleanup(() => container.remove());
  return {start, end, image, map, areas: [...map.children]};
}

async function imageState(image, loaded) {
  const event = new Promise(resolve => {
    image.addEventListener(loaded ? "load" : "error", resolve, {once: true});
  });
  image.src = loaded ? "/images/threecolors.png" : "data:image/png,invalid";
  await event;
  assert_equals(image.naturalWidth > 0, loaded, "Image setup state");
  image.getBoundingClientRect();
}

async function tab(backward = false) {
  const actions = new test_driver.Actions();
  if (backward) {
    actions.keyDown("\uE008");
  }
  actions.keyDown("\uE004").keyUp("\uE004");
  if (backward) {
    actions.keyUp("\uE008");
  }
  await actions.send();
}

async function reaches(destination, backward = false) {
  for (let step = 0; step < 8; ++step) {
    const previous = document.activeElement;
    await tab(backward);
    assert_not_equals(document.activeElement, previous,
                      "Sequential navigation must not stall on a rejected target");
    if (document.activeElement === destination) {
      return;
    }
  }
  assert_unreached("Sequential navigation did not reach the neighboring button");
}

async function crosses(fixture) {
  await test_driver.click(fixture.start);
  await reaches(fixture.end);
  await test_driver.click(fixture.end);
  await reaches(fixture.start, true);
}

async function areaOrder(fixture, areas = fixture.areas) {
  await test_driver.click(fixture.start);
  for (const expected of [...areas, fixture.end]) {
    await tab();
    assert_equals(document.activeElement, expected, "Map forward order");
  }
  for (const expected of [...areas].reverse().concat(fixture.start)) {
    await tab(true);
    assert_equals(document.activeElement, expected, "Map reverse order");
  }
}
