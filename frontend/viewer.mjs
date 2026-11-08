// Three.js GLB viewer: orbit (drag), zoom (wheel/pinch), pan (right-drag). Files are served locally from /vendor.
import * as THREE from 'three';
import { OrbitControls } from '/vendor/three/addons/controls/OrbitControls.js';
import { GLTFLoader } from '/vendor/three/addons/loaders/GLTFLoader.js';

export function createViewer(container) {
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  container.append(renderer.domElement);
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0e1013);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x404048, 2.2));
  const sun = new THREE.DirectionalLight(0xffffff, 1.4);
  sun.position.set(1, 2, 3);
  scene.add(sun);
  const camera = new THREE.PerspectiveCamera(45, 1, 0.001, 1000);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  const loader = new GLTFLoader();
  let model = null, frame = 0;

  function resize() {
    const width = container.clientWidth || 640, height = Math.max(240, Math.round(width * 0.6));
    renderer.setSize(width, height, false);
    renderer.domElement.style.width = '100%';
    renderer.domElement.style.height = 'auto';
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  }
  new ResizeObserver(resize).observe(container);
  resize();

  function render() {
    frame = requestAnimationFrame(render);
    controls.update();
    renderer.render(scene, camera);
  }
  render();

  function frameModel(object) {
    const box = new THREE.Box3().setFromObject(object);
    const size = box.getSize(new THREE.Vector3()).length() || 1, center = box.getCenter(new THREE.Vector3());
    controls.target.copy(center);
    camera.near = size / 1000; camera.far = size * 100;
    camera.position.copy(center).add(new THREE.Vector3(0.6, 0.45, 0.9).normalize().multiplyScalar(size * 1.1));
    camera.updateProjectionMatrix();
    controls.update();
  }

  function stats(object) {
    let triangles = 0, textured = false, vertexColours = false;
    object.traverse(node => {
      if (!node.isMesh) return;
      const geometry = node.geometry;
      triangles += (geometry.index ? geometry.index.count : geometry.attributes.position.count) / 3;
      textured ||= !!node.material?.map;
      vertexColours ||= !!geometry.attributes.color;
    });
    return { triangles: Math.round(triangles), textured, vertexColours };
  }

  return {
    async load(url) {
      const gltf = await loader.loadAsync(url);
      if (model) {
        scene.remove(model);
        model.traverse(node => { node.geometry?.dispose(); [node.material].flat().forEach(m => { m?.map?.dispose(); m?.dispose(); }); });
      }
      model = gltf.scene;
      // Untextured meshes with vertex colours need double-sided lighting to read well from any angle.
      model.traverse(node => { if (node.isMesh) node.material.side = THREE.DoubleSide; });
      scene.add(model);
      frameModel(model);
      return stats(model);
    },
    dispose() { cancelAnimationFrame(frame); renderer.dispose(); },
  };
}
