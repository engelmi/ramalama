import os

from ramalama.common import MNT_DIR, genname, get_accel_env_vars
from ramalama.version import version


class Kube:
    def __init__(self, model, image, args, exec_args):
        self.ai_image = model
        if hasattr(args, "MODEL"):
            self.ai_image = args.MODEL
        self.ai_image = self.ai_image.removeprefix("oci://")
        if hasattr(args, "name") and args.name:
            self.name = args.name
        else:
            self.name = genname()

        self.model = model.removeprefix("oci://")
        self.args = args
        self.exec_args = exec_args
        self.image = image

    def gen_volumes(self):
        mounts = f"""\
        volumeMounts:
        - mountPath: {MNT_DIR}
          subPath: /models
          name: model"""

        volumes = """
      volumes:"""

        if os.path.exists(self.model):
            volumes += self.gen_path_volume()
        else:
            volumes += self.gen_oci_volume()

        m, v = self.gen_devices()
        mounts += m
        volumes += v
        return mounts + volumes

    def gen_devices(self):
        mounts = ""
        volumes = ""
        for dev in ["dri", "kfd"]:
            if os.path.exists("/dev/" + dev):
                mounts += f"""
        - mountPath: /dev/{dev}
          name: {dev}"""
                volumes += f"""
      - hostPath:
          path: /dev/{dev}
        name: {dev}"""
        return mounts, volumes

    def gen_path_volume(self):
        return f"""
      - hostPath:
          path: {self.model}
        name: model"""

    def gen_oci_volume(self):
        return f"""
      - image:
          reference: {self.ai_image}
          pullPolicy: IfNotPresent
        name: model"""

    def _gen_ports(self):
        if not hasattr(self.args, "port"):
            return ""

        p = self.args.port.split(":", 2)
        ports = f"""\
        ports:
        - containerPort: {p[0]}"""
        if len(p) > 1:
            ports += f"""
          hostPort: {p[1]}"""

        return ports

    @staticmethod
    def _gen_env_vars():
        env_vars = get_accel_env_vars()

        if not env_vars:
            return ""

        env_spec = """\
        env:"""

        for k, v in env_vars.items():
            env_spec += f"""
        - name: {k}
          value: {v}"""

        return env_spec

    def generate(self):
        env_string = self._gen_env_vars()
        port_string = self._gen_ports()
        volume_string = self.gen_volumes()
        _version = version()

        outfile = self.name + ".yaml"
        print(f"Generating Kubernetes YAML file: {outfile}")
        with open(outfile, 'w') as c:
            c.write(
                f"""\
# Save the output of this file and use kubectl create -f to import
# it into Kubernetes.
#
# Created with ramalama-{_version}
apiVersion: v1
kind: Deployment
metadata:
  name: {self.name}
  labels:
    app: {self.name}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: {self.name}
  template:
    metadata:
      labels:
        app: {self.name}
    spec:
      containers:
      - name: {self.name}
        image: {self.image}
        command: ["{self.exec_args[0]}"]
        args: {self.exec_args[1:]}
{env_string}
{port_string}
{volume_string}"""
            )
