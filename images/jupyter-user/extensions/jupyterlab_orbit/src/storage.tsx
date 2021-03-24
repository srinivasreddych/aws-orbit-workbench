import React, { useEffect, useState } from 'react';
import { JupyterFrontEnd } from '@jupyterlab/application';
import { ILauncher } from '@jupyterlab/launcher';
import { ReactWidget, ICommandPalette } from '@jupyterlab/apputils';
import { LabIcon } from '@jupyterlab/ui-components';
import { Menu } from '@lumino/widgets';

import { storageIcon } from './common/icons';
import { RUNNING_CLASS, SECTION_CLASS } from './common/styles';
import { CentralWidgetHeader } from './common/headers/centralWidgetHeader';
import { LeftWidgetHeader } from './common/headers/leftWidgetHeader';
import { registerLaunchCommand, registerGeneral } from './common/activation';
import ReactJson from 'react-json-view';
import { request } from './common/backend';
import { IDictionary } from './typings/utils';

const NAME = 'Storage';
const ICON: LabIcon = storageIcon;

// interface IUseItemsReturn {
//   items: JSX.Element;
//   // refreshCallback: () => void;
// }

const refreshCallback = () => {
  console.log(`[${NAME}] Refresh!`);
};

const Item = (props: { item: any }) => (
  <p>
    <ReactJson src={props.item} collapsed={true} displayDataTypes={false}/>
  </p>
);

const Items = (props: { data: any }) => (
  <>
    {' '}
    {props.data.map((x: any) => (
      <Item item={x} />
    ))}{' '}
  </>

);

// const useItems = (type: string): IUseItemsReturn => {
const useItems = (type: string): JSX.Element => {
    console.log('**************Entered useItems type*************');
    console.log('useItems=' + type);
    console.log('**************useItems type*************');
    const [data, setData] = useState([]);

    useEffect(() => {
      const fetchData = async () => {
        console.log('**************useEffect fetchData data*************');
        const parameters: IDictionary<number | string> = {
          type: type
        };
        console.log('**************parameters*************')
        console.log(parameters);
        const data: any[] = await request('storage', parameters);

        console.log(data);
        console.log('**************useEffect fetchData data*************');
        setData(data);

      };
      console.log('Calling fetchData ')
      fetchData();

    }, []);

    // const refreshCallback = async () => {
    //   console.log(`[${NAME}] Refresh!`);
    //   const parameters: IDictionary<number | string> = {
    //     type: type
    //   };
    //   console.log(parameters);
    //   setData(await request('storage', parameters));
    //   console.log('**************refreshCallback data*************');
    //   console.log(data);
    //   console.log('**************refreshCallback data*************');
    // };

    const items = <Items data={data} />;
    // const items = <div><h2>Hello Test</h2></div>;
    console.log('**************useItems Items data*************');
    console.log(data);
    console.log('**************useItems Items data*************');
    return items;
};

const PvcList = (props: { title: string; type: string }): JSX.Element => {
  console.log('******PvcList useItems**********');
  // console.log(props.type);
  console.log('team');
  console.log('****************');
  // eslint-disable-next-line @typescript-eslint/ban-ts-ignore
  // @ts-ignore
  const pvcItemsTest = useItems('team');
  // const pvcItems = <div><h1>PVC List Test</h1></div>;
  // const { pvcItems, refreshCallback } = useItems(props.type);
  return pvcItemsTest;
};

class CentralWidget extends ReactWidget {
  constructor() {
    super();
    this.addClass('jp-ReactWidget');
    this.addClass(RUNNING_CLASS);
    this.title.caption = `AWS Orbit Workbench - ${NAME}`;
    this.title.label = `Orbit - ${NAME}`;
    this.title.icon = ICON;
  }

  render(): JSX.Element {
    return (
      <div className={SECTION_CLASS}>
        <CentralWidgetHeader
          name={NAME}
          icon={ICON}
          refreshCallback={refreshCallback}
        />

        <PvcList title={'Team Persistent Volume Claims'} type={'team'} />
      </div>
    );
  }
}

class LeftWidget extends ReactWidget {
  launchCallback: () => void;

  constructor({ openCallback }: { openCallback: () => void }) {
    super();
    this.addClass('jp-ReactWidget');
    this.addClass(RUNNING_CLASS);
    this.title.caption = `AWS Orbit Workbench - ${NAME}`;
    this.title.icon = ICON;
    this.launchCallback = openCallback;
  }

  render(): JSX.Element {
    return (
      <div className={SECTION_CLASS}>
        <LeftWidgetHeader
          name={NAME}
          icon={ICON}
          refreshCallback={refreshCallback}
          openCallback={this.launchCallback}
        />
        <div />
        <PvcList title={'Team Persistent Volume Claims'} type={'team'} />
      </div>
    );
  }
}

export const activateStorage = (
  app: JupyterFrontEnd,
  palette: ICommandPalette,
  launcher: ILauncher | null,
  menu: Menu,
  rank: number
) => {
  const { commands } = app;

  const launchCommand: string = registerLaunchCommand({
    name: NAME,
    icon: ICON,
    app: app,
    widgetCreation: () => new CentralWidget()
  });

  registerGeneral({
    app: app,
    palette: palette,
    launcher: launcher,
    menu: menu,
    rank: rank,
    launchCommand: launchCommand,
    leftWidget: new LeftWidget({
      openCallback: () => {
        commands.execute(launchCommand);
      }
    })
  });
};
