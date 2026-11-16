export const Role={admin:'业务负责人',analyst:'分析员',viewer:'观察员'};
export const JobStatus={queued:'排队中',running:'计算中',completed:'已完成',failed:'执行失败'};
export const CollectionStatus={
 open:'接收中',waiting:'待补段',conflict:'冲突阻断',sealed:'已成批',
};
export const DeviceStatus={
 receiving:'接收中',waiting:'待补段',blocked:'冲突阻断',done:'已完成',
};
export const RecordState={buffered:'乱序留存',confirmed:'连续确认'};
export const ConflictState={
 open:'待处理',resolved_keep_existing:'已处理·保留原值',resolved_keep_incoming:'已处理·采用新值',
};
